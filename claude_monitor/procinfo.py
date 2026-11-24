"""Cross-platform process discovery (macOS, Linux, Windows) via psutil.

psutil returns argv as a list straight from the OS (KERN_PROCARGS2 on macOS, /proc on Linux, the
PEB on Windows), so arguments containing spaces or JSON keep their exact boundaries. `ps -o args`
does not, and must never be used to rebuild a command.
"""

from __future__ import annotations

import re
from dataclasses import dataclass

import psutil


@dataclass
class Proc:
    pid: int
    ppid: int
    name: str  # executable path when readable, else the process name
    create_time: float  # epoch seconds; with pid, identifies a process across polls
    rss_kb: int
    cpu_seconds: float


def snapshot() -> dict[int, Proc]:
    procs: dict[int, Proc] = {}
    attrs = ["pid", "ppid", "name", "exe", "create_time", "memory_info", "cpu_times"]
    for p in psutil.process_iter(attrs):
        info = p.info
        mem, cpu = info.get("memory_info"), info.get("cpu_times")
        procs[info["pid"]] = Proc(
            pid=info["pid"],
            ppid=info.get("ppid") or 0,
            name=info.get("exe") or info.get("name") or "",
            create_time=info.get("create_time") or 0.0,
            rss_kb=(mem.rss // 1024) if mem else 0,
            cpu_seconds=(cpu.user + cpu.system) if cpu else 0.0,
        )
    return procs


def _safe(fn, default):
    try:
        return fn()
    except (psutil.Error, OSError, ValueError):
        return default


def cmdline(pid: int) -> list[str]:
    return _safe(lambda: psutil.Process(pid).cmdline(), [])


def environ(pid: int) -> dict[str, str]:
    return _safe(lambda: psutil.Process(pid).environ(), {})


def cwd(pid: int) -> str:
    return _safe(lambda: psutil.Process(pid).cwd(), "")


def exe_base(path: str) -> str:
    r"""`claude`, `claude.exe`, `C:\...\claude.EXE`, `/usr/bin/claude` -> `claude`."""
    base = re.split(r"[\\/]", path)[-1].lower()
    return base.removesuffix(".exe")


_HOSTS = [
    ("cmux", re.compile(r"cmux\.app", re.IGNORECASE)),
    ("Orca", re.compile(r"orca", re.IGNORECASE)),
    ("herdr", re.compile(r"herdr|[\\/]\.local[\\/]bin[\\/]hc$", re.IGNORECASE)),
    (
        "Claude desktop",
        re.compile(r"[\\/]Claude\.app[\\/]|[\\/]AnthropicClaude[\\/]", re.IGNORECASE),
    ),
    ("Ghostty", re.compile(r"ghostty", re.IGNORECASE)),
    ("iTerm", re.compile(r"iTerm", re.IGNORECASE)),
    ("Terminal", re.compile(r"[\\/]Terminal\.app[\\/]")),
    ("Windows Terminal", re.compile(r"WindowsTerminal(\.exe)?$", re.IGNORECASE)),
    ("VS Code", re.compile(r"Visual Studio Code|Code Helper|[\\/]Code\.exe$", re.IGNORECASE)),
    ("Cursor", re.compile(r"[\\/]Cursor(\.app|\.exe)", re.IGNORECASE)),
    ("tmux", re.compile(r"(^|[\\/])tmux(: server)?$")),
    ("zellij", re.compile(r"(^|[\\/])zellij$")),
    ("kitty", re.compile(r"(^|[\\/])kitty$")),
    ("WezTerm", re.compile(r"wezterm", re.IGNORECASE)),
    ("GNOME Terminal", re.compile(r"gnome-terminal", re.IGNORECASE)),
    ("Konsole", re.compile(r"(^|[\\/])konsole$")),
    ("Alacritty", re.compile(r"alacritty", re.IGNORECASE)),
]
_APP_BUNDLE = re.compile(r"([^/]+)\.app/")


def host_app(pid: int, table: dict[int, Proc]) -> str:
    """Name the app a process runs under by walking up its parents."""
    seen = set()
    bundle = None
    cur = table.get(pid)
    while cur and cur.ppid and cur.pid not in seen:
        seen.add(cur.pid)
        cur = table.get(cur.ppid)
        if not cur:
            break
        for name, pat in _HOSTS:
            if pat.search(cur.name):
                return name
        m = _APP_BUNDLE.search(cur.name)
        if m and not bundle:
            bundle = m.group(1)
    return bundle or "unknown"


def same_process(pid: int, create_time: float) -> bool:
    """True if this exact process (not a later one reusing the pid) is still running."""
    p = _safe(lambda: psutil.Process(pid), None)
    if p is None:
        return False
    started = _safe(p.create_time, None)
    if started is None or abs(started - create_time) > 0.01:
        return False
    return _safe(lambda: p.status() != psutil.STATUS_ZOMBIE, False)
