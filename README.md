# claude-monitor

A local dashboard of every Claude Code session running on this Mac, with what each one is
doing and buttons to stop, restart or jump to it.

```bash
just dev        # http://localhost:8447  (CLAUDE_MONITOR_PORT to change)
```

Start it **from a cmux tab** if you want Restart and Go to to drive cmux: cmux only accepts
socket commands from processes started inside cmux (or with `CMUX_SOCKET_PASSWORD` set).
Started anywhere else, Restart falls back to a herdr terminal (`hc run`).

## What it shows

For each running `claude` process:

- **Name and title**: the session name from the registry and the AI title from the transcript.
- **Folder, host app and config dir**: the host app is cmux, herdr, Orca, Claude desktop, or
  any other `.app` found among the process's parents. The config dir shows when it isn't
  `~/.claude`.
- **Busy or idle**: as reported by Claude Code.
- **Timing**: when the session started, when you last typed into it (and what), and when it
  last replied. Anything older than 24 h is highlighted.
- **Scheduled jobs**:
  - session-only cron jobs (`CronCreate`), rebuilt from the transcript, because they only
    exist in that process's memory;
  - a pending `/loop` wakeup (`ScheduleWakeup`);
  - durable tasks from `<project>/.claude/scheduled_tasks.json`, and whether this session
    holds the lock that runs them.
- **Process stats**: CPU time, memory, PID and version.
- **Commands**: the exact original command, and a **resume command** that reopens the same
  conversation with the same flags and env (`cd … && CLAUDE_CONFIG_DIR=… claude … --resume <id>`).
- **Remote Control link**: when the session has one.

Sessions stopped from the dashboard, and sessions whose process died without cleaning up its
registry file, appear under **Stopped / exited** with Start and Copy buttons.

## Actions

| Button | Does |
|---|---|
| go to | Focuses the session's cmux workspace and tab (cmux sessions only). |
| copy resume cmd | Copies `cd <dir> && <env> claude <original flags> --resume <session id>`. |
| restart | Sends SIGTERM, waits up to 15 s for a clean exit, then relaunches the resume command. A cmux session goes to a new cmux workspace and a herdr session to a new herdr terminal. If the session will not exit, nothing is relaunched. |
| stop / force | Sends SIGTERM (Claude exits cleanly in about 1 s) or SIGKILL. |
| stop selected | Stops all the ticked sessions. Use "quiet > 24h only" to find forgotten ones. |

Destructive buttons ask for a second click. Restart and Start fall back to copying the
command if no launcher works.

## Where the data comes from

| Data | Source |
|---|---|
| processes, exact argv and env | `ps` + `sysctl(KERN_PROCARGS2)`. `ps` loses the quoting of arguments such as `--mcp-config={…}`. |
| session id, cwd, status, Remote Control id | `<config dir>/sessions/<pid>.json`, matched to the process by start time (`procStart` is UTC) |
| cmux workspace and tab | `cmux sessions list --json` |
| activity, title, cron jobs, wakeups | `<config dir>/projects/*/<session id>.jsonl`, read incrementally |
| durable tasks | `<cwd>/.claude/scheduled_tasks.json` and `.lock` |

Config dirs are `~/.claude`, every `~/.claude-*` with a `sessions/` folder, and any
`CLAUDE_CONFIG_DIR` seen on a running process.

## Safety

- The server binds to `127.0.0.1`.
- Requests whose `Host` header is not localhost are refused, which blocks DNS rebinding.
- Every POST must carry an `X-Claude-Monitor` header, which a cross-site page cannot send.
- Before any signal is sent, the PID's start time is re-checked against the process table, so
  a recycled PID is never killed.
- Commands are built server-side from a session's own argv. The browser never supplies one.
- Env vars whose names look secret (`*KEY*`, `*TOKEN*`, …) are never written into a command.
  The UI lists them by name.

## Development

```bash
just test
just lint
```
