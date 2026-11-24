"""Turn a running session's exact argv + env back into a command that resumes it."""

from __future__ import annotations

import os
import re
import shlex
import shutil

# Flags that pick which conversation to open. They are replaced by `--resume <session id>`.
_RESUME_FLAGS_WITH_VALUE = {"--session-id"}
_RESUME_FLAGS_OPTIONAL_VALUE = {"--resume", "-r"}
_RESUME_FLAGS_BARE = {"--continue", "-c", "--fork-session"}

# Boolean flags: a bare word after one of these is a positional prompt, not the flag's value.
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
_SECRET_ENV = re.compile(r"(KEY|TOKEN|SECRET|PASSWORD|AUTH|CREDENTIAL)", re.IGNORECASE)


def is_print_mode(argv: list[str]) -> bool:
    return any(a in ("-p", "--print") for a in argv[1:])


def launch_env(env: dict[str, str]) -> tuple[dict[str, str], list[str]]:
    """Env vars worth repeating on relaunch, plus the names of secret ones left out."""
    keep, secret = {}, []
    for k, v in sorted(env.items()):
        if _RUNTIME_ENV.match(k) or not _PASSTHROUGH_ENV.match(k):
            continue
        if _SECRET_ENV.search(k):
            secret.append(k)
            continue
        keep[k] = v
    return keep, secret


def _program(argv0: str) -> str:
    """Prefer plain `claude` when argv0 is the installed launcher or a pinned version binary."""
    if "/.local/share/claude/versions/" in argv0 or argv0 == "claude":
        return "claude"
    found = shutil.which("claude")
    if found and os.path.realpath(found) == os.path.realpath(argv0):
        return "claude"
    return argv0


def strip_resume_args(args: list[str]) -> tuple[list[str], str | None]:
    """Remove conversation-selection flags and a trailing positional prompt.

    Returns the remaining args and the dropped prompt (if any) so the UI can mention it.
    """
    out: list[str] = []
    i = 0
    while i < len(args):
        a = args[i]
        flag = a.split("=", 1)[0]
        if flag in _RESUME_FLAGS_BARE:
            i += 1
            continue
        if flag in _RESUME_FLAGS_WITH_VALUE:
            i += 1 if "=" in a else 2
            continue
        if flag in _RESUME_FLAGS_OPTIONAL_VALUE:
            if "=" not in a and i + 1 < len(args) and not args[i + 1].startswith("-"):
                i += 2
            else:
                i += 1
            continue
        out.append(a)
        i += 1
    dropped = None
    if out and not out[-1].startswith("-"):
        prev = out[-2] if len(out) > 1 else None
        if prev is None or prev in _BOOLEAN_FLAGS or "=" in prev:
            dropped = out.pop()
    return out, dropped


def build_command(argv: list[str], env: dict[str, str], cwd: str, session_id: str | None) -> dict:
    """The commands shown and run by the dashboard.

    `original` is what was launched; `resume` reopens the same conversation with the same
    options; `shell` is `resume` prefixed with `cd` so it can be pasted into any terminal.
    """
    keep, secret = launch_env(env)
    env_prefix = " ".join(f"{k}={shlex.quote(v)}" for k, v in keep.items())
    program = _program(argv[0]) if argv else "claude"
    original = " ".join(shlex.quote(a) for a in [program, *argv[1:]])
    args, dropped = strip_resume_args(argv[1:])
    if session_id:
        args += ["--resume", session_id]
    resume = " ".join(shlex.quote(a) for a in [program, *args])
    if env_prefix:
        original = f"{env_prefix} {original}"
        resume = f"{env_prefix} {resume}"
    return {
        "original": original,
        "resume": resume,
        "shell": f"cd {shlex.quote(cwd)} && {resume}" if cwd else resume,
        "dropped_prompt": dropped,
        "secret_env": secret,
        "resumable": bool(session_id) and not is_print_mode(argv),
    }
