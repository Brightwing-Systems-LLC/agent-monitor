# claude-monitor

A local-only Flask dashboard of running coding-agent sessions (Claude Code, Codex, Gemini) on
macOS, Linux and Windows. It is **not** a bwsfoundation project: there is no Django, database or
deploy, and it is never hosted. `just dev` runs it on 127.0.0.1:8447.

Layout:
- `procinfo.py`: psutil snapshot, argv/env/cwd, host app
- `agents/`: one adapter per agent (`matches()` claims processes, `sessions()` builds dicts);
  `base.py` holds the shared session shape and the time-based session matcher
- `gather.py`: dispatches processes to adapters each poll
- `commands.py`: renders argv/env for POSIX shells or PowerShell
- `actions.py`: stop, per-OS launchers, focus, the Stopped store
- `web.py`: API + guards
- `static/index.html` (UI), `static/mascots.js` (sprites and costumes)

Adding an agent = a new `agents/<name>.py` plus a body in `mascots.js` `agentBody()`.

## Traps already paid for

- **Never rebuild argv from `ps -o args`.** cmux launches agents with `--mcp-config={json with
  spaces}`. psutil's `cmdline()` returns the OS's own list; use it.
- **Claude's registry `procStart` is UTC text** with one-second resolution. Compare it to psutil's
  `create_time` as epochs (`agents.claude.registry_matches`), never as strings in local time.
- **Session-only cron jobs are not in `scheduled_tasks.json`.** They live in the Claude process's
  memory and are rebuilt from CronCreate/CronDelete tool calls in the transcript. Jobs created
  before the current process started died with the earlier run of a resumed session.
- **Codex has no per-process registry and does not hold its rollout open.** One process can
  write several rollouts (`/new`), so match the *newest written* rollout in its folder since it
  started, not the one closest to its start time. cmux's record, when present, is exact.
- **Codex rollout `session_meta` is not always line 1**, and its timestamps are UTC.
- **npm-installed Codex runs a vendored binary** (`node_modules/@openai/codex/.../codex`); show
  plain `codex` in commands.
- **Gemini CLI relaunches itself** in a child Node process; report only the innermost one.
- **Gemini cannot resume by id**, only by index or "latest"; resume with `--session-file <chat>`.
- **Starting Gemini can open a browser sign-in and self-update.** Don't launch it in tests.
- **PowerShell `$env:X=...` takes an expression**, so values are always quoted, even "safe" ones.
- **Windows Terminal treats `;` as its own separator.** Launch with `-EncodedCommand`.
- **cmux refuses socket clients not started inside cmux.** Restart falls back to other launchers;
  Go to needs cmux.
- **herdr `hc run` ends a terminal with its parent by default.** Use `--no-parent --outlive-parent`.
- **SIGTERM is a clean exit for Claude Code** (Remote Control torn down, SessionEnd hooks run,
  about 1 s). Windows has no equivalent, so stop there is TerminateProcess.
- Never signal a PID without `actions.verify_identity`; PIDs are recycled.
