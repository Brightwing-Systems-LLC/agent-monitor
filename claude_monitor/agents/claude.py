"""Claude Code sessions.

Sources, joined by PID:
  * each config dir's session registry, <config>/sessions/<pid>.json, written for interactive
    sessions (session id, cwd, status, name, Remote Control id)
  * the transcript, <config>/projects/<dir>/<session id>.jsonl (activity, title, session-only
    cron jobs, /loop wakeups)
  * <cwd>/.claude/scheduled_tasks.json + .lock for durable scheduled tasks
"""

from __future__ import annotations

import calendar
import glob
import json
import os
import re
import time
from pathlib import Path

from .. import commands, procinfo
from ..transcript import active_session_crons, active_wakeup
from .base import HOME, Agent, Caches, Candidate, base_session, short_path

_PINNED = re.compile(r"[\\/]\.local[\\/]share[\\/]claude[\\/]versions[\\/][^\\/]+$")
_DESKTOP = re.compile(r"[\\/]Claude\.app[\\/]|[\\/]AnthropicClaude[\\/]", re.IGNORECASE)

_RESUME_BARE = {"--continue", "-c", "--fork-session"}
_RESUME_WITH_VALUE = {"--session-id"}
_RESUME_OPTIONAL_VALUE = {"--resume", "-r"}
_BOOLEAN_FLAGS = {
    "--dangerously-skip-permissions",
    "--allow-dangerously-skip-permissions",
    "--verbose",
    "--debug",
    "--ide",
    "--chrome",
    "--no-chrome",
    "--strict-mcp-config",
    "--continue",
    "-c",
    "--fork-session",
    "--bare",
    "--disable-slash-commands",
    "--include-partial-messages",
}
_RUNTIME_ENV = re.compile(
    r"^(CLAUDECODE|CLAUDE_PID|CLAUDE_EFFORT|CLAUDE_CODE_(ENTRYPOINT|SESSION.*|SSE_PORT|"
    r"MESSAGING_.*|BRIDGE_.*|EXECPATH|CHILD_.*|SESSION_ATTENDED))$"
)
_PASSTHROUGH_ENV = re.compile(r"^(CLAUDE_|ANTHROPIC_|DISABLE_|ENABLE_|MAX_THINKING|MCP_|BASH_)")


def is_print_mode(argv: list[str]) -> bool:
    return any(a in ("-p", "--print") for a in argv[1:])


def strip_resume_args(args: list[str]) -> tuple[list[str], str | None]:
    return commands.strip_args(
        args,
        bare=_RESUME_BARE,
        with_value=_RESUME_WITH_VALUE,
        optional_value=_RESUME_OPTIONAL_VALUE,
        boolean=_BOOLEAN_FLAGS,
    )


def build_command(argv, env, cwd, session_id, shell=commands.DEFAULT_SHELL) -> dict:
    keep, secret = commands.split_env(env, _PASSTHROUGH_ENV, _RUNTIME_ENV)
    program = commands.short_program(argv[0], "claude", _PINNED) if argv else "claude"
    args, dropped = strip_resume_args(argv[1:])
    resume = (
        [program, *args, "--resume", session_id] if session_id and not is_print_mode(argv) else None
    )
    return commands.command_set([program, *argv[1:]], resume, keep, secret, cwd, dropped, shell)


def config_dirs(extra=frozenset()) -> list[str]:
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


def _utc_epoch(text: str) -> float | None:
    try:
        return calendar.timegm(time.strptime(" ".join(text.split()), "%a %b %d %H:%M:%S %Y"))
    except ValueError:
        return None


def registry_matches(reg: dict, create_time: float) -> bool:
    """True when a registry file describes this exact process, not an earlier one that had the
    same PID. The registry's procStart is UTC text with one-second resolution."""
    proc_start = _utc_epoch(reg.get("procStart") or "")
    if proc_start is not None:
        return abs(proc_start - create_time) <= 2
    started_ms = reg.get("startedAt")
    return bool(started_ms) and 0 <= started_ms / 1000 - create_time <= 600


def session_id_from_argv(argv: list[str]) -> str | None:
    for i, a in enumerate(argv):
        flag, _, val = a.partition("=")
        if flag in ("--session-id", "--resume", "-r"):
            if val:
                return val
            if i + 1 < len(argv) and not argv[i + 1].startswith("-"):
                return argv[i + 1]
    return None


def read_durable_tasks(cwd: str) -> list[dict]:
    try:
        data = json.loads(Path(cwd, ".claude", "scheduled_tasks.json").read_text())
    except (OSError, ValueError):
        return []
    items = data.get("tasks", []) if isinstance(data, dict) else data
    out = []
    for t in items if isinstance(items, list) else []:
        if isinstance(t, dict):
            out.append(
                {
                    "id": str(t.get("id") or ""),
                    "schedule": str(
                        t.get("humanSchedule") or t.get("cron") or t.get("schedule") or ""
                    ),
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


def _job_json(job) -> dict:
    return {
        "id": job.id,
        "schedule": job.schedule,
        "prompt": job.prompt[:300],
        "recurring": job.recurring,
        "next_fire_at": job.next_fire_at,
        "expires_at": job.expires_at(),
        "source": "session",
    }


class Claude(Agent):
    key = "claude"
    label = "Claude Code"

    def __init__(self):
        self.transcript_paths: dict[str, str] = {}
        self.misses: dict[str, float] = {}
        self._stale: list[dict] = []

    def matches(self, proc, argv) -> bool:
        if _DESKTOP.search(proc.name):
            return False
        head = argv[0] if argv else proc.name
        return procinfo.exe_base(head) == "claude" or bool(_PINNED.search(proc.name))

    def _transcript_path(self, config_dir: str, session_id: str) -> str | None:
        key = f"{config_dir}:{session_id}"
        path = self.transcript_paths.get(key)
        if path and os.path.exists(path):
            return path
        if time.time() - self.misses.get(key, 0) < 5:
            return None
        hits = glob.glob(os.path.join(config_dir, "projects", "*", f"{session_id}.jsonl"))
        if not hits:
            self.misses[key] = time.time()
            return None
        path = max(hits, key=os.path.getmtime)
        self.transcript_paths[key] = path
        return path

    def sessions(self, cands: list[Candidate], caches: Caches, now: float) -> list[dict]:
        extra = {
            c.env["CLAUDE_CONFIG_DIR"].rstrip("/\\")
            for c in cands
            if c.env.get("CLAUDE_CONFIG_DIR")
        }
        by_pid = {c.pid: c for c in cands}
        registries: dict[int, tuple[str, dict]] = {}
        self._stale = []
        for cd in config_dirs(extra):
            for pid, reg in read_registry(cd).items():
                c = by_pid.get(pid)
                if c and registry_matches(reg, c.proc.create_time):
                    if c._cwd is None and reg.get("cwd"):
                        c._cwd = reg["cwd"]
                    registries[pid] = (cd, reg)
                elif reg.get("sessionId"):
                    self._stale.append(self._stale_entry(cd, reg))
        out = [self._session(c, registries.get(c.pid), caches, now) for c in cands]
        self._attach_durable_tasks(out)
        return out

    def stale(self, caches, live_pids) -> list[dict]:
        return self._stale

    def _session(self, c: Candidate, registry, caches: Caches, now: float) -> dict:
        s = base_session(self, c)
        config_dir, reg = registry or (None, {})
        if not config_dir:
            config_dir = c.env.get("CLAUDE_CONFIG_DIR", "").rstrip("/\\") or str(HOME / ".claude")
        session_id = (
            reg.get("sessionId") or (c.cmux or {}).get("session_id") or session_id_from_argv(c.argv)
        )
        started_at = (reg.get("startedAt") or 0) / 1000 or c.proc.create_time
        tpath = self._transcript_path(config_dir, session_id) if session_id else None
        state = caches.jsonl(tpath) if tpath else None
        if state:
            s["crons"] = [_job_json(j) for j in active_session_crons(state, started_at, now)]
            w = active_wakeup(state, started_at, now)
            if w:
                s["wakeup"] = {"fire_at": w.fire_at, "reason": w.reason, "prompt": w.prompt[:300]}
        bridge = reg.get("bridgeSessionId")
        s.update(
            {
                "session_id": session_id,
                "name": reg.get("name") or "",
                "title": state.title if state else "",
                "config_dir": config_dir,
                "config_short": short_path(config_dir),
                "version": reg.get("version") or "",
                "kind": reg.get("kind") or ("print" if is_print_mode(c.argv) else "unregistered"),
                "status": reg.get("status") or "",
                "status_at": (reg.get("statusUpdatedAt") or 0) / 1000 or None,
                "started_at": started_at,
                "last_input_at": state.last_input_at if state else None,
                "last_input_text": state.last_input_text if state else "",
                "last_output_at": state.last_output_at if state else None,
                "last_activity_at": max(
                    filter(
                        None,
                        [
                            state.last_event_at if state else None,
                            (reg.get("updatedAt") or 0) / 1000 or None,
                        ],
                    ),
                    default=None,
                ),
                "remote_url": f"https://claude.ai/code/{bridge}" if bridge else None,
                "transcript": tpath,
                "session_match": "registry" if registry else ("cmux" if c.cmux else "argv"),
                "command": build_command(c.argv, c.env, s["cwd"], session_id),
            }
        )
        return s

    def _attach_durable_tasks(self, sessions: list[dict]) -> None:
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

    def _stale_entry(self, config_dir: str, reg: dict) -> dict:
        sid, cwd = reg.get("sessionId"), reg.get("cwd") or ""
        resume = ["claude", "--resume", sid]
        env = {}
        if os.path.realpath(config_dir) != os.path.realpath(HOME / ".claude"):
            env["CLAUDE_CONFIG_DIR"] = config_dir
        return {
            "agent": self.key,
            "agent_label": self.label,
            "session_id": sid,
            "name": reg.get("name") or "",
            "title": "",
            "cwd": cwd,
            "cwd_short": short_path(cwd),
            "config_dir": config_dir,
            "started_at": (reg.get("startedAt") or 0) / 1000 or None,
            "updated_at": (reg.get("updatedAt") or 0) / 1000 or None,
            "pid": reg.get("pid"),
            "host": "",
            "command": commands.command_set(resume, resume, env, [], cwd),
        }
