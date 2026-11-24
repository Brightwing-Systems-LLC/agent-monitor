"""What every agent adapter shares: the running-process record it receives, the session dict it
returns, and the caches it may use between polls."""

from __future__ import annotations

import os
from dataclasses import dataclass, field
from pathlib import Path

from .. import procinfo
from ..transcript import TranscriptReader

HOME = Path.home()


@dataclass
class Candidate:
    """A running process an adapter claimed, with everything read about it once per poll."""

    proc: procinfo.Proc
    argv: list[str]
    table: dict[int, procinfo.Proc]
    cmux: dict | None = None
    _env: dict | None = field(default=None, repr=False)
    _cwd: str | None = field(default=None, repr=False)

    @property
    def pid(self) -> int:
        return self.proc.pid

    @property
    def env(self) -> dict[str, str]:
        if self._env is None:
            self._env = procinfo.environ(self.pid)
        return self._env

    @property
    def cwd(self) -> str:
        if self._cwd is None:
            self._cwd = (self.cmux or {}).get("cwd") or procinfo.cwd(self.pid)
        return self._cwd


class Caches:
    """Per-file readers and parsed indexes that survive between polls."""

    def __init__(self):
        self.readers: dict[str, TranscriptReader] = {}
        self.memo: dict[str, tuple[float, object]] = {}

    def jsonl(self, path: str, factory=None, apply=None):
        reader = self.readers.get(path)
        if reader is None:
            reader = self.readers[path] = TranscriptReader(path, factory, apply)
        return reader.refresh()

    def by_mtime(self, path: str, load):
        """`load(path)`, re-run only when the file's mtime changes."""
        try:
            mtime = os.path.getmtime(path)
        except OSError:
            return None
        hit = self.memo.get(path)
        if hit and hit[0] == mtime:
            return hit[1]
        value = load(path)
        self.memo[path] = (mtime, value)
        return value


class Agent:
    key = ""
    label = ""

    def matches(self, proc: procinfo.Proc, argv: list[str]) -> bool:
        raise NotImplementedError

    def sessions(self, cands: list[Candidate], caches: Caches, now: float) -> list[dict]:
        raise NotImplementedError

    def stale(self, caches: Caches, live_pids: set[int]) -> list[dict]:
        """Sessions whose process died without cleaning up (agents that keep a registry)."""
        return []


def short_path(path: str | None) -> str:
    if not path:
        return ""
    home = str(HOME)
    return "~" + path[len(home) :] if path.startswith(home) else path


def base_session(agent: Agent, c: Candidate) -> dict:
    """Every field the UI reads, with neutral defaults; adapters fill in what they know."""
    parent = c.table.get(c.proc.ppid)
    return {
        "agent": agent.key,
        "agent_label": agent.label,
        "pid": c.pid,
        "proc_start": c.proc.create_time,
        "session_id": None,
        "name": "",
        "title": "",
        "cwd": c.cwd,
        "cwd_short": short_path(c.cwd),
        "config_dir": "",
        "config_short": "",
        "version": "",
        "kind": "interactive",
        "status": "",
        "status_at": None,
        "host": procinfo.host_app(c.pid, c.table),
        "cmux": (
            {"workspace_id": c.cmux.get("workspace_id"), "surface_id": c.cmux.get("surface_id")}
            if c.cmux
            else None
        ),
        "started_at": c.proc.create_time,
        "last_input_at": None,
        "last_input_text": "",
        "last_output_at": None,
        "last_activity_at": None,
        "cpu_seconds": c.proc.cpu_seconds,
        "rss_mb": round(c.proc.rss_kb / 1024),
        "crons": [],
        "wakeup": None,
        "durable_tasks": [],
        "remote_url": None,
        "transcript": None,
        "session_match": "",
        "argv": c.argv,
        "command": None,
        "parent_is_agent": bool(parent) and parent.pid != c.pid and _is_agent_name(parent.name),
    }


def _is_agent_name(name: str) -> bool:
    return procinfo.exe_base(name) in ("claude", "codex", "gemini")


def assign_by_time(procs: list[tuple[int, float]], files: list[dict]) -> dict[int, dict]:
    """Match processes to session files in one folder when nothing names the session.

    `procs` are (pid, start time); `files` have `path`, `started` and `updated` epochs. A process
    is working on the session it most recently wrote, which it either created (started after the
    process did, e.g. a fresh launch or /new) or resumed (started earlier, written since). Files
    go out newest-written first, each to the latest-started eligible process that predates it.
    """
    out: dict[int, dict] = {}
    for f in sorted(files, key=lambda f: -f["updated"]):
        eligible = [
            (pid, st)
            for pid, st in procs
            if pid not in out and (f["started"] >= st - 5 or f["updated"] >= st - 5)
        ]
        if not eligible:
            continue
        before = [e for e in eligible if e[1] <= f["started"] + 5]
        pid, st = max(before or eligible, key=lambda e: e[1])
        out[pid] = f | {
            "match": "new in this process"
            if f["started"] >= st - 5
            else "resumed (matched by time)"
        }
    return out
