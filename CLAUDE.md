# claude-monitor

A local-only Flask dashboard of running Claude Code sessions. It is **not** a bwsfoundation
project: there is no Django, database or deploy, and it is never hosted. `just dev` runs it on
127.0.0.1:8447.

Layout: `procinfo.py` (processes) → `gather.py` (joins everything into session dicts) →
`web.py` (API + guards) → `static/index.html` (the whole UI). `actions.py` does stop, launch and
focus. `transcript.py` and `commands.py` are pure and are where most tests live.

## Traps already paid for

- **`ps` loses argv quoting.** cmux launches claude with `--mcp-config={json with spaces}`.
  Read argv and env from `procinfo.procargs` (KERN_PROCARGS2), never from `ps -o args`.
- **Registry `procStart` is UTC; `ps -o lstart` is local time.** Comparing them as strings
  matches nothing and every session looks unregistered. Use `gather.registry_matches`.
- **Session-only cron jobs are not in `scheduled_tasks.json`.** They live in the process's
  memory and are rebuilt from CronCreate/CronDelete tool calls in the transcript. Jobs created
  before the current process started died with the earlier run of a resumed session.
- **cmux refuses socket clients not started inside cmux** ("Access denied - only processes
  started inside cmux can connect"). Restart falls back to herdr; Go to needs cmux.
- **herdr `hc run` ends a terminal with its parent by default.** Launch with `--no-parent
  --outlive-parent`, or the relaunched session dies when the dashboard stops.
- **SIGTERM is a clean exit for Claude Code**: Remote Control is torn down and SessionEnd hooks
  run within about 1 s. SIGKILL skips all of that, so Force is a separate button.
- Never signal a PID without `actions.verify_identity`; PIDs are recycled.
