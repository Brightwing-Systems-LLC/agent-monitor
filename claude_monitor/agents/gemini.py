"""Google Gemini CLI sessions.

Chats: ~/.gemini/tmp/<project>/chats/session-*.json, one JSON document per session (rewritten as
it grows), where <project> comes from ~/.gemini/projects.json (folder -> name) or, in older
versions, the sha256 of the folder. Gemini can resume by index or "latest" but not by id, so the
resume command loads the chat with `--session-file`.

The CLI is a Node script and relaunches itself in a child process with a larger heap; only the
innermost process of such a pair is reported.
"""

from __future__ import annotations

import hashlib
import json
import re
import shutil
from pathlib import Path

from .. import commands, procinfo
from ..transcript import parse_ts
from .base import HOME, Agent, Caches, Candidate, assign_by_time, base_session

_SCRIPT = re.compile(r"(^|[\\/])gemini(\.js|\.mjs|\.cjs)?$|@google[\\/]gemini-cli", re.IGNORECASE)
_RUNTIMES = {"node", "bun", "deno"}
_NON_INTERACTIVE = {
    "-p",
    "--prompt",
    "--list-sessions",
    "--list-extensions",
    "-l",
    "--list-models",
    "--delete-session",
    "-v",
    "--version",
    "-h",
    "--help",
}
_SUBCOMMANDS = {"mcp", "extensions", "models", "skills"}
_SESSION_FLAGS = {"--resume", "-r", "--session-id", "--session-file", "-i", "--prompt-interactive"}
_BOOLEAN = {
    "-y",
    "--yolo",
    "-d",
    "--debug",
    "-s",
    "--sandbox",
    "--screen-reader",
    "--skip-trust",
    "--checkpointing",
    "--experimental-acp",
}
_PASSTHROUGH_ENV = re.compile(
    r"^(GEMINI_[A-Z_]+|GOOGLE_CLOUD_PROJECT|GOOGLE_CLOUD_LOCATION|GOOGLE_GENAI_USE_VERTEXAI)$"
)


def split_program(argv: list[str]) -> tuple[list[str], list[str]]:
    """(program words, gemini args): `node /x/gemini --yolo` -> (['node', '/x/gemini'], ...)."""
    if argv and procinfo.exe_base(argv[0]) in _RUNTIMES:
        for i, a in enumerate(argv[1:], start=1):
            if _SCRIPT.search(a):
                return argv[: i + 1], argv[i + 1 :]
        return argv[:1], argv[1:]
    return argv[:1], argv[1:]


def _flag_value(args: list[str], *names: str) -> str | None:
    for i, a in enumerate(args):
        flag, _, val = a.partition("=")
        if flag in names:
            return val or (args[i + 1] if i + 1 < len(args) else None)
    return None


def build_command(argv, env, cwd, chat_path, shell=commands.DEFAULT_SHELL) -> dict:
    prog, args = split_program(argv)
    keep, secret = commands.split_env(env, _PASSTHROUGH_ENV)
    if procinfo.exe_base(prog[0]) == "gemini":
        program = [commands.short_program(prog[0], "gemini")]
    else:
        program = ["gemini"] if shutil.which("gemini") else prog
    rest, dropped = commands.strip_args(args, with_value=_SESSION_FLAGS, boolean=_BOOLEAN)
    resume = [*program, *rest, "--session-file", chat_path] if chat_path else None
    return commands.command_set([*program, *args], resume, keep, secret, cwd, dropped, shell)


def gemini_home(env: dict) -> Path:
    return (
        Path(env["GEMINI_CLI_HOME"]) / ".gemini"
        if env.get("GEMINI_CLI_HOME")
        else (HOME / ".gemini")
    )


def chats_dir(home: Path, cwd: str, projects: dict) -> Path:
    name = projects.get(cwd) or hashlib.sha256(cwd.encode()).hexdigest()
    return home / "tmp" / name / "chats"


def read_chat(path: str) -> dict | None:
    try:
        data = json.loads(Path(path).read_text(encoding="utf-8"))
    except (OSError, ValueError):
        return None
    msgs = data.get("messages") or []
    out = {
        "id": data.get("sessionId"),
        "started": parse_ts(data.get("startTime")) or 0.0,
        "updated": parse_ts(data.get("lastUpdated")) or 0.0,
        "title": "",
        "last_input_at": None,
        "last_input_text": "",
        "last_output_at": None,
    }
    for m in msgs:
        ts = parse_ts(m.get("timestamp"))
        content = m.get("content")
        text = (
            content
            if isinstance(content, str)
            else " ".join(p.get("text", "") for p in content or [] if isinstance(p, dict))
        )
        if m.get("type") == "user":
            out["last_input_at"], out["last_input_text"] = ts, text[:400]
            if not out["title"] and text.strip():
                out["title"] = text.strip().splitlines()[0][:80]
        elif m.get("type") == "gemini":
            out["last_output_at"] = ts
    return out


def _projects(home: Path) -> dict:
    try:
        return json.loads((home / "projects.json").read_text()).get("projects", {})
    except (OSError, ValueError, AttributeError):
        return {}


class Gemini(Agent):
    key = "gemini"
    label = "Gemini"

    def matches(self, proc, argv) -> bool:
        prog, args = split_program(argv)
        if not prog or not (
            procinfo.exe_base(prog[0]) == "gemini" or (len(prog) > 1 and _SCRIPT.search(prog[-1]))
        ):
            return False
        if any(a.split("=", 1)[0] in _NON_INTERACTIVE for a in args):
            return "-i" in args or "--prompt-interactive" in args
        return not (args and args[0] in _SUBCOMMANDS)

    def sessions(self, cands: list[Candidate], caches: Caches, now: float) -> list[dict]:
        pids = {c.pid for c in cands}
        cands = [
            c
            for c in cands
            if not any(o.proc.ppid == c.pid for o in cands if o.pid in pids and o.pid != c.pid)
        ]
        out = []
        groups: dict[tuple[Path, str], list[Candidate]] = {}
        for c in cands:
            groups.setdefault((gemini_home(c.env), c.cwd), []).append(c)
        for (home, cwd), group in groups.items():
            folder = chats_dir(home, cwd, _projects(home))
            files = []
            for p in sorted(folder.glob("session-*.json")):
                chat = caches.by_mtime(str(p), read_chat)
                if chat and chat["id"]:
                    files.append(chat | {"path": str(p)})
            named, unnamed = {}, []
            for c in group:
                _, args = split_program(c.argv)
                sid = _flag_value(args, "--session-id")
                spath = _flag_value(args, "--session-file")
                hit = next(
                    (
                        f
                        for f in files
                        if (sid and f["id"] == sid) or (spath and Path(f["path"]) == Path(spath))
                    ),
                    None,
                )
                if hit:
                    named[c.pid] = hit | {"match": "argv"}
                else:
                    unnamed.append(c)
            taken = {h["path"] for h in named.values()}
            named.update(
                assign_by_time(
                    [(c.pid, c.proc.create_time) for c in unnamed],
                    [f for f in files if f["path"] not in taken],
                )
            )
            for c in group:
                out.append(self._session(c, named.get(c.pid)))
        return out

    def _session(self, c: Candidate, chat: dict | None) -> dict:
        s = base_session(self, c)
        if chat:
            s.update(
                {
                    "session_id": chat["id"],
                    "title": chat["title"],
                    "last_input_at": chat["last_input_at"],
                    "last_input_text": chat["last_input_text"],
                    "last_output_at": chat["last_output_at"],
                    "last_activity_at": chat["updated"],
                    "transcript": chat["path"],
                    "session_match": chat.get("match", ""),
                }
            )
        s["command"] = build_command(c.argv, c.env, s["cwd"], chat["path"] if chat else None)
        return s
