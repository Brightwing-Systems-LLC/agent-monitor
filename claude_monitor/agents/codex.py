"""OpenAI Codex CLI sessions.

Codex keeps no per-process registry and does not hold its transcript open, so a process is tied
to its session by, in order: `codex resume <id>` in argv, cmux's hook record, or the newest
rollout written in the same folder since the process started (see base.assign_by_time).

Transcripts: $CODEX_HOME/sessions/YYYY/MM/DD/rollout-<time>-<id>.jsonl. Titles:
$CODEX_HOME/session_index.jsonl.
"""

from __future__ import annotations

import json
import os
import re
from dataclasses import dataclass
from pathlib import Path

from .. import commands, procinfo
from ..transcript import parse_ts
from .base import HOME, Agent, Caches, Candidate, assign_by_time, base_session

_PINNED = re.compile(
    r"[\\/]\.codex[\\/]packages[\\/]standalone[\\/]releases[\\/]|node_modules[\\/]@openai[\\/]codex"
)
_WITH_VALUE = {
    "-c",
    "--config",
    "--enable",
    "--disable",
    "--remote",
    "--remote-auth-token-env",
    "-i",
    "--image",
    "-m",
    "--model",
    "--local-provider",
    "-p",
    "--profile",
    "-s",
    "--sandbox",
    "-C",
    "--cd",
    "--add-dir",
    "-a",
    "--ask-for-approval",
}
_SUBCOMMANDS = {
    "agents",
    "exec",
    "e",
    "review",
    "login",
    "logout",
    "mcp",
    "mcp-server",
    "plugin",
    "app-server",
    "remote-control",
    "app",
    "completion",
    "update",
    "doctor",
    "sandbox",
    "debug",
    "apply",
    "a",
    "resume",
    "queue",
    "archive",
    "delete",
    "migrate-rollouts",
    "migrate-nfs-db",
    "unarchive",
    "fork",
    "cloud",
    "exec-server",
    "help",
}
_INTERACTIVE = {None, "resume", "fork"}
_DROP_FLAGS = {"--last", "--all"}
_PASSTHROUGH_ENV = re.compile(r"^(CODEX_HOME|OPENAI_[A-Z0-9_]+)$")


def parse_argv(argv: list[str]) -> tuple[list[str], str | None, list[str]]:
    """Split codex argv into (options, subcommand, positionals)."""
    opts: list[str] = []
    sub = None
    pos: list[str] = []
    i = 1
    while i < len(argv):
        a = argv[i]
        if a.startswith("-"):
            opts.append(a)
            if a in _WITH_VALUE and i + 1 < len(argv):
                opts.append(argv[i + 1])
                i += 1
        elif sub is None and not pos and a in _SUBCOMMANDS:
            sub = a
        else:
            pos.append(a)
        i += 1
    return opts, sub, pos


def build_command(argv, env, cwd, session_id, shell=commands.DEFAULT_SHELL) -> dict:
    opts, sub, pos = parse_argv(argv)
    keep, secret = commands.split_env(env, _PASSTHROUGH_ENV)
    program = commands.short_program(argv[0], "codex", _PINNED) if argv else "codex"
    opts = [o for o in opts if o not in _DROP_FLAGS]
    dropped = (
        pos[-1] if pos and sub is None else (pos[1] if sub == "resume" and len(pos) > 1 else None)
    )
    resume = [program, "resume", *opts, session_id] if session_id else None
    return commands.command_set([program, *argv[1:]], resume, keep, secret, cwd, dropped, shell)


@dataclass
class CodexState:
    last_input_at: float | None = None
    last_input_text: str = ""
    last_output_at: float | None = None
    last_event_at: float | None = None
    busy: bool | None = None
    status_at: float | None = None
    version: str = ""


def _first_text(content) -> str:
    if isinstance(content, str):
        return content
    for part in content or []:
        if isinstance(part, dict) and part.get("text"):
            return part["text"]
    return ""


def apply_record(s: CodexState, rec: dict) -> None:
    ts = parse_ts(rec.get("timestamp"))
    kind = rec.get("type")
    p = rec.get("payload") if isinstance(rec.get("payload"), dict) else {}
    ptype = p.get("type")
    if ts:
        s.last_event_at = max(ts, s.last_event_at or 0)
    if kind == "session_meta":
        s.version = p.get("cli_version") or s.version
    elif kind == "event_msg" and ptype == "task_started":
        s.busy, s.status_at = True, ts
    elif kind == "event_msg" and ptype in ("task_complete", "turn_aborted"):
        s.busy, s.status_at = False, ts
    elif kind == "response_item" and ptype == "message":
        text = _first_text(p.get("content"))
        if p.get("role") == "user" and text and not text.lstrip().startswith("<") and ts:
            s.last_input_at, s.last_input_text = ts, text[:400]
        elif p.get("role") == "assistant" and ts:
            s.last_output_at = ts


def _session_meta(path: str) -> dict | None:
    """id / cwd / start time from the session_meta record near the top of a rollout."""
    try:
        with open(path, "rb") as fh:
            for _ in range(5):
                line = fh.readline()
                if not line:
                    break
                rec = json.loads(line)
                p = rec.get("payload")
                if rec.get("type") == "session_meta" and isinstance(p, dict) and p.get("id"):
                    return {
                        "id": p["id"],
                        "cwd": p.get("cwd") or "",
                        "started": parse_ts(p.get("timestamp")) or 0.0,
                    }
    except (OSError, ValueError):
        return None
    return None


def _read_index(path: str) -> dict[str, str]:
    names = {}
    try:
        with open(path, encoding="utf-8") as fh:
            for line in fh:
                try:
                    rec = json.loads(line)
                except ValueError:
                    continue
                if rec.get("id") and rec.get("thread_name"):
                    names[rec["id"]] = rec["thread_name"]
    except OSError:
        pass
    return names


class Codex(Agent):
    key = "codex"
    label = "Codex"

    def __init__(self):
        self.meta: dict[str, dict | None] = {}

    def matches(self, proc, argv) -> bool:
        head = argv[0] if argv else proc.name
        if procinfo.exe_base(head) != "codex":
            return False
        return parse_argv(argv)[1] in _INTERACTIVE

    def _rollouts(self, home: Path) -> list[dict]:
        out = []
        for path in (home / "sessions").glob("*/*/*/rollout-*.jsonl"):
            p = str(path)
            if p not in self.meta:
                self.meta[p] = _session_meta(p)
            meta = self.meta[p]
            if not meta:
                continue
            try:
                updated = os.path.getmtime(p)
            except OSError:
                continue
            out.append(meta | {"path": p, "updated": updated})
        return out

    def sessions(self, cands: list[Candidate], caches: Caches, now: float) -> list[dict]:
        out = []
        by_home: dict[Path, list[Candidate]] = {}
        for c in cands:
            by_home.setdefault(Path(c.env.get("CODEX_HOME") or HOME / ".codex"), []).append(c)
        for home, group in by_home.items():
            rollouts = self._rollouts(home)
            by_id = {r["id"]: r for r in rollouts}
            titles = caches.by_mtime(str(home / "session_index.jsonl"), _read_index) or {}
            chosen: dict[int, tuple[dict | None, str]] = {}
            unnamed = []
            for c in group:
                _, sub, pos = parse_argv(c.argv)
                sid = (pos[0] if sub == "resume" and pos else None) or (c.cmux or {}).get(
                    "session_id"
                )
                if sid:
                    chosen[c.pid] = (by_id.get(sid) or {"id": sid}, "argv" if sub else "cmux")
                else:
                    unnamed.append(c)
            taken = {r["id"] for r, _ in chosen.values() if r}
            by_cwd: dict[str, list[Candidate]] = {}
            for c in unnamed:
                by_cwd.setdefault(c.cwd, []).append(c)
            for cwd, cs in by_cwd.items():
                files = [r for r in rollouts if r["cwd"] == cwd and r["id"] not in taken]
                got = assign_by_time([(c.pid, c.proc.create_time) for c in cs], files)
                for c in cs:
                    hit = got.get(c.pid)
                    chosen[c.pid] = (hit, hit["match"] if hit else "")
            for c in group:
                rollout, how = chosen.get(c.pid, (None, ""))
                out.append(self._session(c, rollout, how, titles, caches))
        return out

    def _session(self, c, rollout, how, titles, caches) -> dict:
        s = base_session(self, c)
        sid = (rollout or {}).get("id")
        path = (rollout or {}).get("path")
        state = caches.jsonl(path, CodexState, apply_record) if path else None
        if state:
            s.update(
                {
                    "version": state.version,
                    "status": "" if state.busy is None else ("busy" if state.busy else "idle"),
                    "status_at": state.status_at,
                    "last_input_at": state.last_input_at,
                    "last_input_text": state.last_input_text,
                    "last_output_at": state.last_output_at,
                    "last_activity_at": state.last_event_at,
                }
            )
        s.update(
            {
                "session_id": sid,
                "title": titles.get(sid, "") if sid else "",
                "config_dir": c.env.get("CODEX_HOME", ""),
                "transcript": path,
                "session_match": how,
                "command": build_command(c.argv, c.env, s["cwd"], sid),
            }
        )
        return s
