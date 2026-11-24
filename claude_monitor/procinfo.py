"""Process discovery on macOS: the process table, exact argv/env, and which app hosts a process.

`ps` joins argv with spaces, which loses quoting (cmux passes `--mcp-config={json with spaces}`),
so argv and env come from sysctl(KERN_PROCARGS2), which returns them NUL-separated. It only works
for processes owned by the same user, which is every Claude session we care about.
"""

from __future__ import annotations

import ctypes
import ctypes.util
import os
import re
import struct
import subprocess
from dataclasses import dataclass

_libc = ctypes.CDLL(ctypes.util.find_library("c"), use_errno=True)
_CTL_KERN, _KERN_ARGMAX, _KERN_PROCARGS2 = 1, 8, 49
_VERSIONS_RE = re.compile(r"/\.local/share/claude/versions/[^/]+$")


@dataclass
class Proc:
    pid: int
    ppid: int
    rss_kb: int
    cpu_seconds: float
    lstart: str  # normalised `ps -o lstart` text, e.g. "Tue Nov 24 13:23:25 2026"
    comm: str


def _argmax() -> int:
    mib = (ctypes.c_int * 2)(_CTL_KERN, _KERN_ARGMAX)
    val = ctypes.c_int(0)
    size = ctypes.c_size_t(ctypes.sizeof(val))
    if _libc.sysctl(mib, 2, ctypes.byref(val), ctypes.byref(size), None, 0) != 0:
        return 1024 * 1024
    return val.value


def parse_procargs2(raw: bytes) -> tuple[list[str], dict[str, str]]:
    """Split a KERN_PROCARGS2 buffer: int argc, exec path, NUL padding, argv[argc], env..., NUL."""
    if len(raw) < 4:
        return [], {}
    argc = struct.unpack_from("i", raw, 0)[0]
    pos = raw.find(b"\0", 4)
    if pos < 0:
        return [], {}
    while pos < len(raw) and raw[pos] == 0:
        pos += 1
    parts = raw[pos:].split(b"\0")
    argv = [p.decode("utf-8", "replace") for p in parts[:argc]]
    env: dict[str, str] = {}
    for p in parts[argc:]:
        if not p:
            break
        key, sep, val = p.decode("utf-8", "replace").partition("=")
        if sep:
            env[key] = val
    return argv, env


def procargs(pid: int) -> tuple[list[str], dict[str, str]] | None:
    size = _argmax()
    buf = ctypes.create_string_buffer(size)
    length = ctypes.c_size_t(size)
    mib = (ctypes.c_int * 3)(_CTL_KERN, _KERN_PROCARGS2, pid)
    if _libc.sysctl(mib, 3, buf, ctypes.byref(length), None, 0) != 0:
        return None
    return parse_procargs2(buf.raw[: length.value])


def _cpu_seconds(text: str) -> float:
    days = 0
    if "-" in text:
        d, text = text.split("-", 1)
        days = int(d)
    secs = 0.0
    for part in text.split(":"):
        secs = secs * 60 + float(part)
    return days * 86400 + secs


def normalise_lstart(text: str) -> str:
    return " ".join(text.split())


def parse_ps(output: str) -> dict[int, Proc]:
    procs: dict[int, Proc] = {}
    for line in output.splitlines():
        bits = line.split(None, 9)
        if len(bits) < 10:
            continue
        try:
            pid, ppid, rss = int(bits[0]), int(bits[1]), int(bits[2])
        except ValueError:
            continue
        procs[pid] = Proc(
            pid,
            ppid,
            rss,
            _cpu_seconds(bits[3]),
            normalise_lstart(" ".join(bits[4:9])),
            bits[9].strip(),
        )
    return procs


def process_table() -> dict[int, Proc]:
    out = subprocess.run(
        ["ps", "-Ao", "pid=,ppid=,rss=,time=,lstart=,comm="],
        capture_output=True,
        text=True,
        check=False,
    ).stdout
    return parse_ps(out)


def is_claude_cli(comm: str) -> bool:
    """The Claude Code CLI binary, not the desktop app (`Claude`) or a wrapper shell."""
    return os.path.basename(comm) == "claude" or bool(_VERSIONS_RE.search(comm))


_HOSTS = [
    ("cmux", re.compile(r"/cmux\.app/", re.IGNORECASE)),
    ("Orca", re.compile(r"orca", re.IGNORECASE)),
    ("herdr", re.compile(r"herdr|/\.local/bin/hc$", re.IGNORECASE)),
    ("Claude desktop", re.compile(r"/Claude\.app/")),
    ("Ghostty", re.compile(r"ghostty", re.IGNORECASE)),
    ("iTerm", re.compile(r"iTerm", re.IGNORECASE)),
    ("Terminal", re.compile(r"/Terminal\.app/")),
    ("VS Code", re.compile(r"Visual Studio Code|Code Helper", re.IGNORECASE)),
    ("Cursor", re.compile(r"Cursor", re.IGNORECASE)),
    ("tmux", re.compile(r"(^|/)tmux")),
    ("zellij", re.compile(r"(^|/)zellij")),
]


def host_app(pid: int, table: dict[int, Proc]) -> str:
    """Name the app a process runs under by walking up its parents."""
    seen = set()
    bundle = None
    cur = table.get(pid)
    while cur and cur.ppid > 1 and cur.pid not in seen:
        seen.add(cur.pid)
        cur = table.get(cur.ppid)
        if not cur:
            break
        for name, pat in _HOSTS:
            if pat.search(cur.comm):
                return name
        m = _APP_BUNDLE.search(cur.comm)
        if m and not bundle:
            bundle = m.group(1)
    return bundle or "unknown"


_APP_BUNDLE = re.compile(r"([^/]+)\.app/")
