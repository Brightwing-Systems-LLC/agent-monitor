"""Build the dashboard's view of every Claude Code session on this machine.

Sources, joined by PID:
  * the process table + exact argv/env (procinfo)
  * each config dir's session registry, <config>/sessions/<pid>.json, which Claude Code writes
    for interactive sessions (session id, cwd, status, Remote Control id)
  * cmux's hook records (`cmux sessions list --json`), for the workspace/tab a session lives in
  * the session transcript (activity, title, session-only cron jobs, /loop wakeups)
  * <cwd>/.claude/scheduled_tasks.json + .lock for durable scheduled tasks
"""

from __future__ import annotations

import calendar
import copy
import glob
import json
import os
import subprocess
import threading
import time
from pathlib import Path

from . import commands, procinfo
from .transcript import TranscriptReader, active_session_crons, active_wakeup

HOME = Path.home()
CMUX = "/Applications/cmux.app/Contents/Resources/bin/cmux"


def config_dirs(extra: set[str] = frozenset()) -> list[str]:
    dirs = {str(HOME / ".claude")} | set(extra)
    for d in HOME.glob(".claude-*"):
        if (d / "sessions").is_dir():
            dirs.add(str(d))
    return sorted(d for d in dirs if os.path.isdir(d))


def read_registry(config_dir: str) -> dict[int, dict]:
    out = {}
    for f in glob.glob(os.path.join(config_dir, "sessions", "*.json")):
        try:
            data = json.loads(Path(f).read_text())
            out[int(data["pid"])] = data | {"_file": f}
        except (OSError, ValueError, KeyError, TypeError):
            continue
    return out


def _cwd_of(pid: int) -> str:
    out = subprocess.run(
        ["lsof", "-a", "-d", "cwd", "-p", str(pid), "-Fn"],
        capture_output=True,
        text=True,
        check=False,
    ).stdout
    for line in out.splitlines():
        if line.startswith("n"):
            return line[1:]
    return ""


def _session_id_from_argv(argv: list[str]) -> str | None:
    for i, a in enumerate(argv):
        flag, _, val = a.partition("=")
        if flag in ("--session-id", "--resume", "-r"):
            if val:
                return val
            if i + 1 < len(argv) and not argv[i + 1].startswith("-"):
                return argv[i + 1]
    return None


class Monitor:
    """Holds per-transcript readers and slow-changing caches between polls."""

    def __init__(self):
        self.readers: dict[str, TranscriptReader] = {}
        self.transcript_paths: dict[str, str] = {}
        self.transcript_misses: dict[str, float] = {}
        self.cmux_cache: tuple[float, dict[int, dict]] = (0.0, {})
        self.lock = threading.Lock()
        self.last: tuple[float, dict | None] = (0.0, None)

    def cmux_records(self) -> dict[int, dict]:
        stamp, cached = self.cmux_cache
        if time.time() - stamp < 10 or not os.path.exists(CMUX):
            return cached
        try:
            out = subprocess.run(
                [CMUX, "sessions", "list", "--json", "--agent", "claude", "--all"],
                capture_output=True,
                text=True,
                timeout=10,
                check=False,
            ).stdout
            rows = json.loads(out).get("sessions", [])
        except (OSError, ValueError, subprocess.TimeoutExpired):
            rows = []
        recs = {}
        for r in rows:
            if r.get("pid") and r.get("agent_lifecycle") != "ended":
                recs.setdefault(int(r["pid"]), r)
        self.cmux_cache = (time.time(), recs)
        return recs

    def transcript_path(self, config_dir: str, session_id: str) -> str | None:
        key = f"{config_dir}:{session_id}"
        path = self.transcript_paths.get(key)
        if path and os.path.exists(path):
            return path
        if time.time() - self.transcript_misses.get(key, 0) < 5:
            return None
        hits = glob.glob(os.path.join(config_dir, "projects", "*", f"{session_id}.jsonl"))
        if not hits:
            self.transcript_misses[key] = time.time()
            return None
        path = max(hits, key=os.path.getmtime)
        self.transcript_paths[key] = path
        return path

    def transcript(self, path: str):
        reader = self.readers.get(path)
        if reader is None:
            reader = self.readers[path] = TranscriptReader(path)
        return reader.refresh()

    def gather(self, max_age: float = 2.0) -> dict:
        """A scan costs ~1-2s with dozens of sessions; open tabs share one recent result."""
        with self.lock:
            stamp, cached = self.last
            if cached is None or time.time() - stamp > max_age:
                cached = self._gather()
                self.last = (time.time(), cached)
            return copy.deepcopy(cached)

    def _gather(self) -> dict:
        now = time.time()
        table = procinfo.process_table()
        cli = {pid: p for pid, p in table.items() if procinfo.is_claude_cli(p.comm)}

        details = {}
        extra_dirs = set()
        for pid in cli:
            got = procinfo.procargs(pid)
            if got:
                details[pid] = got
                cd = got[1].get("CLAUDE_CONFIG_DIR")
                if cd:
                    extra_dirs.add(cd.rstrip("/"))

        registries: dict[int, tuple[str, dict]] = {}
        stale: list[tuple[str, dict]] = []
        for cd in config_dirs(extra_dirs):
            for pid, reg in read_registry(cd).items():
                proc = cli.get(pid)
                if proc and registry_matches(reg, proc):
                    registries[pid] = (cd, reg)
                else:
                    stale.append((cd, reg))

        cmux = self.cmux_records()
        sessions = [
            self._session(
                pid, proc, table, details.get(pid), registries.get(pid), cmux.get(pid), now
            )
            for pid, proc in cli.items()
        ]
        self._attach_durable_tasks(sessions, now)
        sessions.sort(key=lambda s: -(s["last_activity_at"] or s["started_at"] or 0))

        stopped = [self._stale_entry(cd, reg) for cd, reg in stale]
        stopped = [s for s in stopped if s]
        return {"generated_at": now, "sessions": sessions, "stale": stopped}

    def _session(self, pid, proc, table, detail, registry, cmux_rec, now) -> dict:
        argv, env = detail or ([proc.comm], {})
        config_dir, reg = registry or (None, {})
        if not config_dir:
            config_dir = env.get("CLAUDE_CONFIG_DIR", "").rstrip("/") or str(HOME / ".claude")
        session_id = (
            reg.get("sessionId")
            or (cmux_rec or {}).get("session_id")
            or _session_id_from_argv(argv)
        )
        cwd = reg.get("cwd") or (cmux_rec or {}).get("cwd") or _cwd_of(pid)
        started_at = (reg.get("startedAt") or 0) / 1000 or _lstart_epoch(proc.lstart)

        state = None
        tpath = self.transcript_path(config_dir, session_id) if session_id else None
        if tpath:
            state = self.transcript(tpath)

        crons, wakeup = [], None
        if state:
            crons = [_job_json(j, "session") for j in active_session_crons(state, started_at, now)]
            w = active_wakeup(state, started_at, now)
            if w:
                wakeup = {"fire_at": w.fire_at, "reason": w.reason, "prompt": w.prompt[:300]}

        cmd = commands.build_command(argv, env, cwd, session_id)
        host = procinfo.host_app(pid, table)
        bridge = reg.get("bridgeSessionId")
        last_activity = max(
            filter(
                None,
                [
                    state.last_event_at if state else None,
                    (reg.get("updatedAt") or 0) / 1000 or None,
                ],
            ),
            default=None,
        )
        return {
            "pid": pid,
            "lstart": proc.lstart,
            "session_id": session_id,
            "name": reg.get("name") or "",
            "title": state.title if state else "",
            "cwd": cwd,
            "cwd_short": _short(cwd),
            "config_dir": config_dir,
            "config_short": _short(config_dir),
            "version": reg.get("version") or "",
            "kind": reg.get("kind")
            or ("print" if commands.is_print_mode(argv) else "unregistered"),
            "status": reg.get("status") or "",
            "status_at": (reg.get("statusUpdatedAt") or 0) / 1000 or None,
            "host": host,
            "cmux": (
                {
                    "workspace_id": cmux_rec.get("workspace_id"),
                    "surface_id": cmux_rec.get("surface_id"),
                }
                if cmux_rec
                else None
            ),
            "started_at": started_at,
            "last_input_at": state.last_input_at if state else None,
            "last_input_text": state.last_input_text if state else "",
            "last_output_at": state.last_output_at if state else None,
            "last_activity_at": last_activity,
            "cpu_seconds": proc.cpu_seconds,
            "rss_mb": round(proc.rss_kb / 1024),
            "crons": crons,
            "wakeup": wakeup,
            "durable_tasks": [],
            "remote_url": f"https://claude.ai/code/{bridge}" if bridge else None,
            "transcript": tpath,
            "argv": argv,
            "command": cmd,
            "parent_is_claude": procinfo.is_claude_cli(table[proc.ppid].comm)
            if proc.ppid in table
            else False,
        }

    def _attach_durable_tasks(self, sessions: list[dict], now: float) -> None:
        by_cwd: dict[str, list[dict]] = {}
        for s in sessions:
            if s["cwd"]:
                by_cwd.setdefault(s["cwd"], []).append(s)
        for cwd, group in by_cwd.items():
            tasks = read_durable_tasks(cwd)
            if not tasks:
                continue
            owner = read_task_lock(cwd)
            for s in group:
                runs_them = bool(owner) and owner.get("pid") == s["pid"]
                s["durable_tasks"] = [t | {"runner": runs_them} for t in tasks]

    def _stale_entry(self, config_dir: str, reg: dict) -> dict | None:
        sid, cwd = reg.get("sessionId"), reg.get("cwd") or ""
        if not sid:
            return None
        return {
            "session_id": sid,
            "name": reg.get("name") or "",
            "cwd": cwd,
            "cwd_short": _short(cwd),
            "config_dir": config_dir,
            "started_at": (reg.get("startedAt") or 0) / 1000 or None,
            "updated_at": (reg.get("updatedAt") or 0) / 1000 or None,
            "registry_file": reg.get("_file"),
            "pid": reg.get("pid"),
        }


def read_durable_tasks(cwd: str) -> list[dict]:
    path = os.path.join(cwd, ".claude", "scheduled_tasks.json")
    try:
        data = json.loads(Path(path).read_text())
    except (OSError, ValueError):
        return []
    items = data.get("tasks", []) if isinstance(data, dict) else data
    out = []
    for t in items if isinstance(items, list) else []:
        if not isinstance(t, dict):
            continue
        out.append(
            {
                "id": str(t.get("id") or ""),
                "schedule": str(t.get("humanSchedule") or t.get("cron") or t.get("schedule") or ""),
                "prompt": str(t.get("prompt") or "")[:300],
                "recurring": bool(t.get("recurring", True)),
                "source": "file",
            }
        )
    return out


def read_task_lock(cwd: str) -> dict | None:
    try:
        return json.loads(Path(cwd, ".claude", "scheduled_tasks.lock").read_text())
    except (OSError, ValueError):
        return None


def _job_json(job, source: str) -> dict:
    return {
        "id": job.id,
        "schedule": job.schedule,
        "prompt": job.prompt[:300],
        "recurring": job.recurring,
        "next_fire_at": job.next_fire_at,
        "expires_at": job.expires_at(),
        "source": source,
    }


def _lstart_epoch(lstart: str) -> float | None:
    """`ps -o lstart` is local time."""
    try:
        return time.mktime(time.strptime(" ".join(lstart.split()), "%a %b %d %H:%M:%S %Y"))
    except ValueError:
        return None


def _utc_epoch(text: str) -> float | None:
    try:
        return calendar.timegm(time.strptime(" ".join(text.split()), "%a %b %d %H:%M:%S %Y"))
    except ValueError:
        return None


def registry_matches(reg: dict, proc) -> bool:
    """True when a registry file describes this exact process, not an earlier one with the
    same PID. The registry's procStart is UTC; `ps` lstart is local time."""
    started = _lstart_epoch(proc.lstart)
    if started is None:
        return False
    proc_start = _utc_epoch(reg.get("procStart") or "")
    if proc_start is not None:
        return abs(proc_start - started) <= 2
    started_ms = reg.get("startedAt")
    return bool(started_ms) and 0 <= started_ms / 1000 - started <= 600


def _short(path: str | None) -> str:
    if not path:
        return ""
    home = str(HOME)
    return "~" + path[len(home) :] if path.startswith(home) else path
