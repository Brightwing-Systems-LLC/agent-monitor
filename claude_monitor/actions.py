"""Stop, restart, start and focus sessions.

Every action is keyed by (pid, lstart) from the last poll, re-checked against the live process
table before a signal is sent, so a recycled PID is never signalled. Commands are only ever
built server-side from a session's own argv; the browser never supplies a command.

Launchers:
  cmux  - `cmux new-workspace`. cmux only accepts socket clients started inside cmux (or with
          CMUX_SOCKET_PASSWORD), so this works when the dashboard was started from a cmux tab.
  herdr - `hc run --bg --no-parent`, a herdr terminal that outlives the dashboard.
"""

from __future__ import annotations

import json
import os
import shlex
import shutil
import signal
import subprocess
import threading
import time
from pathlib import Path

from . import procinfo

CMUX = "/Applications/cmux.app/Contents/Resources/bin/cmux"
HC = shutil.which("hc") or str(Path.home() / ".local/bin/hc")
STATE_DIR = Path(
    os.environ.get("CLAUDE_MONITOR_STATE", Path.home() / ".local/state/claude-monitor")
)
STOP_WAIT_S = 15


class ActionError(Exception):
    pass


def verify_identity(pid: int, lstart: str) -> None:
    table = procinfo.process_table()
    proc = table.get(pid)
    if not proc:
        raise ActionError(f"pid {pid} is no longer running")
    if proc.lstart != procinfo.normalise_lstart(lstart) or not procinfo.is_claude_cli(proc.comm):
        raise ActionError(f"pid {pid} is now a different process; refresh and try again")


def alive(pid: int) -> bool:
    try:
        os.kill(pid, 0)
    except ProcessLookupError:
        return False
    except PermissionError:
        return True
    return True


def stop(pid: int, lstart: str, force: bool = False) -> None:
    verify_identity(pid, lstart)
    os.kill(pid, signal.SIGKILL if force else signal.SIGTERM)


def wait_exit(pid: int, timeout: float = STOP_WAIT_S) -> bool:
    deadline = time.time() + timeout
    while time.time() < deadline:
        if not alive(pid):
            return True
        time.sleep(0.25)
    return not alive(pid)


def _workspace_name(session: dict) -> str:
    return session.get("name") or os.path.basename(session.get("cwd") or "") or "claude"


def launch_cmux(session: dict) -> str:
    cmd = session["command"]["resume"]
    proc = subprocess.run(
        [
            CMUX,
            "new-workspace",
            "--name",
            _workspace_name(session),
            "--cwd",
            session["cwd"],
            "--command",
            cmd,
        ],
        capture_output=True,
        text=True,
        timeout=20,
        check=False,
        env=os.environ | {"CMUX_QUIET": "1"},
    )
    if proc.returncode != 0:
        raise ActionError("cmux: " + (proc.stderr or proc.stdout).strip().splitlines()[-1])
    return "cmux"


def launch_herdr(session: dict) -> str:
    # A login shell so PATH and the user's profile match a terminal they opened themselves;
    # it stays open afterwards so a failed launch is readable rather than vanishing.
    script = f"{session['command']['resume']}; exec ${{SHELL:-/bin/zsh}} -l"
    proc = subprocess.run(
        [
            HC,
            "run",
            "--bg",
            "--no-parent",
            "--outlive-parent",
            "--cwd",
            session["cwd"],
            "--",
            os.environ.get("SHELL", "/bin/zsh"),
            "-lic",
            script,
        ],
        capture_output=True,
        text=True,
        timeout=20,
        check=False,
    )
    if proc.returncode != 0:
        raise ActionError("herdr: " + (proc.stderr or proc.stdout).strip()[-300:])
    return "herdr"


def launchers_for(host: str) -> list:
    order = []
    if host == "herdr":
        order = [launch_herdr, launch_cmux]
    else:
        order = [launch_cmux, launch_herdr]
    return [f for f in order if os.path.exists(CMUX if f is launch_cmux else HC)]


def launch(session: dict) -> str:
    if not session.get("cwd") or not session.get("command", {}).get("resumable"):
        raise ActionError("this session cannot be resumed (no session id, or print mode)")
    errors = []
    for fn in launchers_for(session.get("host", "")):
        try:
            return fn(session)
        except (ActionError, OSError, subprocess.TimeoutExpired) as e:
            errors.append(str(e))
    raise ActionError("; ".join(errors) or "no launcher available (cmux or herdr)")


def focus(session: dict) -> None:
    rec = session.get("cmux")
    if not rec:
        raise ActionError("only cmux tabs can be focused")
    ws, surface = rec["workspace_id"], rec["surface_id"]
    for argv in (
        [CMUX, "select-workspace", "--workspace", ws],
        [CMUX, "focus-panel", "--panel", surface, "--workspace", ws],
    ):
        p = subprocess.run(argv, capture_output=True, text=True, timeout=10, check=False)
        if p.returncode != 0:
            raise ActionError("cmux: " + (p.stderr or p.stdout).strip().splitlines()[-1])
    subprocess.run(["open", "-a", "cmux"], check=False)


class StoppedStore:
    """Sessions stopped from the dashboard, kept so they can be started again later."""

    def __init__(self, path: Path = STATE_DIR / "stopped.json"):
        self.path = path
        self.lock = threading.Lock()

    def load(self) -> list[dict]:
        try:
            return json.loads(self.path.read_text())
        except (OSError, ValueError):
            return []

    def _save(self, items: list[dict]) -> None:
        self.path.parent.mkdir(parents=True, exist_ok=True)
        tmp = self.path.with_suffix(".tmp")
        tmp.write_text(json.dumps(items, indent=1))
        tmp.replace(self.path)

    def add(self, session: dict) -> None:
        keep = {
            k: session.get(k)
            for k in (
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
        }
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


def basic_resume_command(session_id: str, cwd: str, config_dir: str) -> dict:
    """For a session whose process is gone and whose argv we never saw (stale registry file)."""
    env = ""
    if config_dir and os.path.realpath(config_dir) != os.path.realpath(Path.home() / ".claude"):
        env = f"CLAUDE_CONFIG_DIR={shlex.quote(config_dir)} "
    resume = f"{env}claude --resume {shlex.quote(session_id)}"
    return {
        "original": resume,
        "resume": resume,
        "shell": f"cd {shlex.quote(cwd)} && {resume}",
        "dropped_prompt": None,
        "secret_env": [],
        "resumable": True,
    }
