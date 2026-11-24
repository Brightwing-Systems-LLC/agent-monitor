"""Render a session's argv + env as a command line for POSIX shells or PowerShell.

Agent adapters decide *which* argv resumes a session; this module only turns argv/env/cwd into
text a user can paste (or a launcher can run) on the current platform.
"""

from __future__ import annotations

import os
import re
import shlex
import shutil
import sys

POSIX, PWSH = "posix", "powershell"
DEFAULT_SHELL = PWSH if sys.platform == "win32" else POSIX

_SECRET_ENV = re.compile(r"(KEY|TOKEN|SECRET|PASSWORD|AUTH|CREDENTIAL)", re.IGNORECASE)
_PWSH_BARE = re.compile(r"^[A-Za-z0-9_./:\\=+,@%-]+$")


def quote(arg: str, shell: str = DEFAULT_SHELL) -> str:
    if shell == PWSH:
        # PowerShell single quotes are literal; the only escape is '' for '. A leading '-'
        # stays bare so PowerShell passes it through as a flag to the native program.
        if arg and _PWSH_BARE.match(arg) and not arg.startswith(("@", "=")):
            return arg
        return "'" + arg.replace("'", "''") + "'"
    return shlex.quote(arg)


def render(argv: list[str], env: dict[str, str], shell: str = DEFAULT_SHELL) -> str:
    if shell == PWSH:
        # The right side of an assignment is an expression, so values are always quoted.
        prefix = "".join(f"$env:{k}='{v.replace(chr(39), chr(39) * 2)}'; " for k, v in env.items())
        head = quote(argv[0], PWSH)
        call = "& " + head if head.startswith("'") else head
        return prefix + " ".join([call, *(quote(a, PWSH) for a in argv[1:])])
    prefix = "".join(f"{k}={shlex.quote(v)} " for k, v in env.items())
    return prefix + " ".join(shlex.quote(a) for a in argv)


def with_cd(cwd: str, command: str, shell: str = DEFAULT_SHELL) -> str:
    if not cwd:
        return command
    if shell == PWSH:
        return f"Set-Location -LiteralPath {quote(cwd, PWSH)}; {command}"
    return f"cd {shlex.quote(cwd)} && {command}"


def split_env(env: dict[str, str], passthrough: re.Pattern, runtime: re.Pattern | None = None):
    """Env vars worth repeating on relaunch, plus the names of secret ones left out."""
    keep, secret = {}, []
    for k, v in sorted(env.items()):
        if (runtime and runtime.match(k)) or not passthrough.match(k):
            continue
        if _SECRET_ENV.search(k):
            secret.append(k)
            continue
        keep[k] = v
    return keep, secret


def short_program(argv0: str, name: str, pinned: re.Pattern | None = None) -> str:
    """Prefer the bare command name when argv0 is what `name` resolves to on PATH, or a pinned
    version binary of it (which would relaunch an old version)."""
    base = re.split(r"[\\/]", argv0)[-1].lower().removesuffix(".exe")
    if argv0 == name or (pinned and pinned.search(argv0)):
        return name
    found = shutil.which(name)
    if base == name and found and os.path.realpath(found) == os.path.realpath(argv0):
        return name
    return argv0


def strip_args(args: list[str], *, bare=(), with_value=(), optional_value=(), boolean=()):
    """Remove session-selection flags, and a trailing positional prompt.

    Returns the remaining args and the dropped prompt (if any) so the UI can mention it.
    """
    out: list[str] = []
    i = 0
    while i < len(args):
        a = args[i]
        flag = a.split("=", 1)[0]
        if flag in bare:
            i += 1
        elif flag in with_value:
            i += 1 if "=" in a else 2
        elif flag in optional_value:
            has_value = "=" not in a and i + 1 < len(args) and not args[i + 1].startswith("-")
            i += 2 if has_value else 1
        else:
            out.append(a)
            i += 1
    dropped = None
    if out and not out[-1].startswith("-"):
        prev = out[-2] if len(out) > 1 else None
        if prev is None or prev in boolean or "=" in prev:
            dropped = out.pop()
    return out, dropped


def command_set(
    original_argv: list[str],
    resume_argv: list[str] | None,
    env: dict[str, str],
    secret_env: list[str],
    cwd: str,
    dropped_prompt: str | None = None,
    shell: str = DEFAULT_SHELL,
) -> dict:
    """What the dashboard shows and runs. `resume` reopens the session with the same options;
    `shell` is `resume` with a change of directory, ready to paste into any terminal."""
    original = render(original_argv, env, shell)
    resume = render(resume_argv, env, shell) if resume_argv else ""
    return {
        "original": original,
        "resume": resume,
        "shell": with_cd(cwd, resume or original, shell),
        "shell_kind": shell,
        "dropped_prompt": dropped_prompt,
        "secret_env": secret_env,
        "resumable": bool(resume_argv),
    }
