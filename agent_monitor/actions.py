"""Stop, restart, start and focus sessions, on macOS, Linux and Windows.

Every action is keyed by (pid, process start time) from the last poll and re-checked against the
live process before anything is signalled, so a recycled PID is never touched. Commands are only
ever built server-side from a session's own argv; the browser never supplies a command.

Stopping: SIGTERM on macOS/Linux lets the agent exit cleanly; Windows has no equivalent for a
console process in another console, so "stop" there is TerminateProcess, the same as force.

Launchers, tried in order (the session's own host first when it has a launcher):
  macOS   cmux workspace, herdr terminal, Terminal.app window
  Linux   herdr terminal, tmux window (if a server is running), a GUI terminal emulator
  Windows Windows Terminal tab, a new PowerShell console
cmux only accepts socket clients started inside cmux (or with CMUX_SOCKET_PASSWORD), so start
the dashboard from a cmux tab to drive it.
"""

from __future__ import annotations

import base64
import json
import os
import shlex
import shutil
import subprocess
import sys
import threading
import time
from pathlib import Path

import psutil

from . import procinfo
from .agents import BY_KEY

IS_WIN, IS_MAC = sys.platform == "win32", sys.platform == "darwin"
CMUX = "/Applications/cmux.app/Contents/Resources/bin/cmux"
HC = shutil.which("hc") or str(Path.home() / ".local" / "bin" / "hc")


def _state_dir() -> Path:
    if os.environ.get("AGENT_MONITOR_STATE"):
        return Path(os.environ["AGENT_MONITOR_STATE"])
    if IS_WIN:
        return Path(os.environ.get("LOCALAPPDATA", Path.home())) / "agent-monitor"
    return Path(os.environ.get("XDG_STATE_HOME", Path.home() / ".local" / "state")) / (
        "agent-monitor"
    )


STATE_DIR = _state_dir()
STOP_WAIT_S = 15


class ActionError(Exception):
    pass


def verify_identity(pid: int, proc_start: float, agent: str) -> psutil.Process:
    if not procinfo.same_process(pid, proc_start):
        raise ActionError(f"pid {pid} is no longer running that session; refresh and try again")
    table = procinfo.snapshot()
    proc = table.get(pid)
    adapter = BY_KEY.get(agent)
    if not proc or not adapter or not adapter.matches(proc, procinfo.cmdline(pid)):
        raise ActionError(f"pid {pid} is now a different process; refresh and try again")
    return psutil.Process(pid)


def stop(pid: int, proc_start: float, agent: str, force: bool = False) -> None:
    p = verify_identity(pid, proc_start, agent)
    try:
        p.kill() if force else p.terminate()
    except psutil.NoSuchProcess:
        pass


def wait_exit(pid: int, proc_start: float, timeout: float = STOP_WAIT_S) -> bool:
    deadline = time.time() + timeout
    while time.time() < deadline:
        if not procinfo.same_process(pid, proc_start):
            return True
        time.sleep(0.25)
    return not procinfo.same_process(pid, proc_start)


def _name(session: dict) -> str:
    return (
        session.get("name")
        or os.path.basename(session.get("cwd") or "")
        or session.get("agent")
        or "agent"
    )


def _posix_shell() -> str:
    return os.environ.get("SHELL") or ("/bin/zsh" if IS_MAC else "/bin/bash")


def _keep_open(session: dict) -> str:
    # A login shell so PATH and profile match a terminal the user opened; it stays open after
    # the agent exits so a failed launch is readable instead of vanishing.
    return f"{session['command']['resume']}; exec {shlex.quote(_posix_shell())} -l"


def _check(proc: subprocess.CompletedProcess, who: str) -> None:
    if proc.returncode != 0:
        text = (proc.stderr or proc.stdout or "").strip()
        raise ActionError(
            f"{who}: " + (text.splitlines()[-1] if text else f"exit {proc.returncode}")
        )


def _spawn(argv: list[str], who: str, **kw) -> None:
    """Start a GUI terminal detached; fail only if it dies straight away."""
    try:
        p = subprocess.Popen(argv, stdout=subprocess.DEVNULL, stderr=subprocess.PIPE, **kw)
    except OSError as e:
        raise ActionError(f"{who}: {e}") from e
    try:
        code = p.wait(timeout=1.5)
    except subprocess.TimeoutExpired:
        return
    if code != 0:
        err = p.stderr.read().decode(errors="replace").strip() if p.stderr else ""
        raise ActionError(f"{who}: {err.splitlines()[-1] if err else f'exit {code}'}")


def launch_cmux(session: dict) -> str:
    _check(
        subprocess.run(
            [
                CMUX,
                "new-workspace",
                "--name",
                _name(session),
                "--cwd",
                session["cwd"],
                "--command",
                session["command"]["resume"],
            ],
            capture_output=True,
            text=True,
            timeout=20,
            check=False,
            env=os.environ | {"CMUX_QUIET": "1"},
        ),
        "cmux",
    )
    return "cmux"


def launch_herdr(session: dict) -> str:
    _check(
        subprocess.run(
            [
                HC,
                "run",
                "--bg",
                "--no-parent",
                "--outlive-parent",
                "--cwd",
                session["cwd"],
                "--",
                _posix_shell(),
                "-lic",
                _keep_open(session),
            ],
            capture_output=True,
            text=True,
            timeout=20,
            check=False,
        ),
        "herdr",
    )
    return "herdr"


def _applescript_string(text: str) -> str:
    return '"' + text.replace("\\", "\\\\").replace('"', '\\"') + '"'


def launch_terminal_app(session: dict) -> str:
    script = (
        f'tell application "Terminal"\nactivate\n'
        f"do script {_applescript_string(session['command']['shell'])}\nend tell"
    )
    _check(
        subprocess.run(
            ["osascript", "-e", script], capture_output=True, text=True, timeout=20, check=False
        ),
        "Terminal",
    )
    return "Terminal"


def _tmux_running() -> bool:
    if not shutil.which("tmux"):
        return False
    return subprocess.run(["tmux", "has-session"], capture_output=True, check=False).returncode == 0


def launch_tmux(session: dict) -> str:
    inner = f"{shlex.quote(_posix_shell())} -lic {shlex.quote(_keep_open(session))}"
    _check(
        subprocess.run(
            ["tmux", "new-window", "-c", session["cwd"], "-n", _name(session), inner],
            capture_output=True,
            text=True,
            timeout=20,
            check=False,
        ),
        "tmux",
    )
    return "tmux"


_LINUX_TERMINALS = [
    (
        "gnome-terminal",
        lambda cwd, sh, cmd: [
            "gnome-terminal",
            f"--working-directory={cwd}",
            "--",
            sh,
            "-lic",
            cmd,
        ],
    ),
    ("konsole", lambda cwd, sh, cmd: ["konsole", "--workdir", cwd, "-e", sh, "-lic", cmd]),
    ("kitty", lambda cwd, sh, cmd: ["kitty", "--directory", cwd, sh, "-lic", cmd]),
    ("wezterm", lambda cwd, sh, cmd: ["wezterm", "start", "--cwd", cwd, "--", sh, "-lic", cmd]),
    (
        "alacritty",
        lambda cwd, sh, cmd: ["alacritty", "--working-directory", cwd, "-e", sh, "-lic", cmd],
    ),
    ("xterm", lambda cwd, sh, cmd: ["xterm", "-e", sh, "-lic", f"cd {shlex.quote(cwd)}; {cmd}"]),
]


def launch_linux_terminal(session: dict) -> str:
    for name, build in _LINUX_TERMINALS:
        if shutil.which(name):
            _spawn(
                build(session["cwd"], _posix_shell(), _keep_open(session)),
                name,
                start_new_session=True,
            )
            return name
    raise ActionError(
        "no terminal emulator found (tried gnome-terminal, konsole, kitty, "
        "wezterm, alacritty, xterm)"
    )


def _powershell() -> str:
    return shutil.which("pwsh") or shutil.which("powershell") or "powershell.exe"


def _encoded(command: str) -> str:
    return base64.b64encode(command.encode("utf-16-le")).decode()


def launch_windows_terminal(session: dict) -> str:
    wt = shutil.which("wt") or shutil.which("wt.exe")
    if not wt:
        raise ActionError("Windows Terminal (wt.exe) not found")
    # -EncodedCommand sidesteps wt's own ';' command separator and every quoting layer.
    _spawn(
        [
            wt,
            "-w",
            "0",
            "new-tab",
            "-d",
            session["cwd"],
            _powershell(),
            "-NoExit",
            "-EncodedCommand",
            _encoded(session["command"]["resume"]),
        ],
        "Windows Terminal",
    )
    return "Windows Terminal"


def launch_windows_console(session: dict) -> str:
    _spawn(
        [_powershell(), "-NoExit", "-EncodedCommand", _encoded(session["command"]["resume"])],
        "PowerShell",
        cwd=session["cwd"],
        creationflags=getattr(subprocess, "CREATE_NEW_CONSOLE", 0),
    )
    return "PowerShell"


def launchers_for(host: str, platform: str = sys.platform) -> list:
    if platform == "win32":
        return [launch_windows_terminal, launch_windows_console]
    own = {"cmux": launch_cmux, "herdr": launch_herdr, "tmux": launch_tmux}
    if platform == "darwin":
        order = [launch_cmux, launch_herdr, launch_terminal_app]
    else:
        order = [launch_herdr, launch_tmux, launch_linux_terminal]
    if host in own and own[host] in order:
        order.remove(own[host])
        order.insert(0, own[host])
    available = {
        launch_cmux: os.path.exists(CMUX),
        launch_herdr: os.path.exists(HC),
        launch_tmux: _tmux_running() if platform != "darwin" else False,
    }
    return [f for f in order if available.get(f, True)]


def launch(session: dict) -> str:
    if not session.get("cwd") or not (session.get("command") or {}).get("resumable"):
        raise ActionError("this session cannot be resumed (no session found, or print mode)")
    errors = []
    for fn in launchers_for(session.get("host", "")):
        try:
            return fn(session)
        except (ActionError, OSError, subprocess.TimeoutExpired) as e:
            errors.append(str(e))
    raise ActionError("; ".join(errors) or "no launcher available")


def focus(session: dict) -> None:
    rec = session.get("cmux")
    if not rec or not os.path.exists(CMUX):
        raise ActionError("only cmux tabs can be focused")
    ws, surface = rec["workspace_id"], rec["surface_id"]
    for argv in (
        [CMUX, "select-workspace", "--workspace", ws],
        [CMUX, "focus-panel", "--panel", surface, "--workspace", ws],
    ):
        _check(
            subprocess.run(argv, capture_output=True, text=True, timeout=10, check=False), "cmux"
        )
    subprocess.run(["open", "-a", "cmux"], check=False)


class StoppedStore:
    """Sessions stopped from the dashboard, kept so they can be started again later."""

    FIELDS = (
        "agent",
        "agent_label",
        "session_id",
        "name",
        "title",
        "cwd",
        "cwd_short",
        "config_dir",
        "config_short",
        "host",
        "command",
        "last_activity_at",
    )

    def __init__(self, path: Path | None = None):
        self.path = path or STATE_DIR / "stopped.json"
        self.lock = threading.Lock()

    def load(self) -> list[dict]:
        try:
            return json.loads(self.path.read_text(encoding="utf-8"))
        except (OSError, ValueError):
            return []

    def _save(self, items: list[dict]) -> None:
        self.path.parent.mkdir(parents=True, exist_ok=True)
        tmp = self.path.with_suffix(".tmp")
        tmp.write_text(json.dumps(items, indent=1), encoding="utf-8")
        tmp.replace(self.path)

    def add(self, session: dict) -> None:
        keep = {k: session.get(k) for k in self.FIELDS}
        keep["stopped_at"] = time.time()
        with self.lock:
            items = [i for i in self.load() if i.get("session_id") != keep["session_id"]]
            cutoff = time.time() - 14 * 86400
            items = [keep] + [i for i in items if i.get("stopped_at", 0) > cutoff][:99]
            self._save(items)

    def remove(self, session_id: str) -> None:
        with self.lock:
            self._save([i for i in self.load() if i.get("session_id") != session_id])

    def get(self, session_id: str) -> dict | None:
        return next((i for i in self.load() if i.get("session_id") == session_id), None)
