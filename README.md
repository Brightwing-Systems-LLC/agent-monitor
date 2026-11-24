# agent-monitor

A local dashboard of every coding-agent session running on your machine (**Claude Code**,
**OpenAI Codex CLI** and **Google Gemini CLI**), with what each one is doing and buttons to
stop, restart or jump to it.

> **Unofficial.** agent-monitor is an independent project. It is not made, endorsed or
> supported by Anthropic, OpenAI or Google. "Claude", "Claude Code" and the Claude Code mascot
> are Anthropic's trademarks and artwork; "Codex" and "Gemini" are trademarks of OpenAI and
> Google. They are used here only to identify the sessions being monitored.

```bash
just dev        # http://localhost:8447  (AGENT_MONITOR_PORT to change)
```

Runs on **macOS, Linux and Windows** (Python 3.11+, [uv](https://docs.astral.sh/uv/)). Without
`just`: `uv run agent-monitor`.

Start it from your own terminal, not from an agent's shell tool. A server started by an agent
dies when that agent exits or is stopped, and the page then shows it as offline. The session
that started it is marked **runs this monitor**.

## What it shows

For each running agent process:

- **Agent, title and name.** A pixel mascot per agent (the Claude Code crab, a Codex terminal, a
  Gemini sparkle) in a costume fixed to that session. Browse the costumes at `/#gallery`.
- **Folder, host app and config dir.** The host app is the program the session runs inside:
  cmux, herdr, tmux, Windows Terminal, VS Code, Orca, or any other macOS `.app` found among the
  process's parents.
- **Busy or idle.** Reported by Claude Code; read from turn events for Codex.
- **Timing.** When the session started, when you last typed into it (and what), and when it last
  replied. Anything older than 24 h is highlighted.
- **Scheduled jobs (Claude Code only):**
  - session-only cron jobs (`CronCreate`), rebuilt from the transcript, because they only exist
    in that process's memory;
  - a pending `/loop` wakeup;
  - durable tasks from `<project>/.claude/scheduled_tasks.json`, and whether this session holds
    the lock that runs them.
- **Process stats.** CPU time, memory, PID and version.
- **Commands.** The exact original command, and a **resume command** that reopens the same
  session with the same flags and env, rendered for your shell (POSIX or PowerShell).
- **Warnings.** The same session open in two processes, or a session matched by timing rather
  than an id (see below).
- **Remote Control link** (Claude Code).

Sessions stopped from the dashboard, and Claude Code sessions whose process died without
cleaning up, appear under **Stopped / exited** with Start and Copy buttons.

## Actions

| Button | Does |
|---|---|
| go to | Focuses the session's cmux workspace and tab (cmux sessions only). |
| copy resume cmd | Copies the command that changes directory and resumes the session. |
| restart | Stops the session, waits up to 15 s for a clean exit, then relaunches it with the resume command. If it will not exit, nothing is relaunched. |
| stop / force | SIGTERM (a clean exit) or SIGKILL. On Windows both end the process immediately. |
| stop selected | Stops all ticked sessions. Use "quiet > 24h only" to find forgotten ones. |

Relaunches open in the first launcher that works, starting with the session's own host:

| OS | Launchers |
|---|---|
| macOS | cmux workspace, herdr terminal, Terminal.app |
| Linux | herdr terminal, tmux window (when a tmux server is running), GNOME Terminal / Konsole / kitty / WezTerm / Alacritty / xterm |
| Windows | Windows Terminal tab, a new PowerShell console |

cmux only accepts commands from processes started inside cmux (or with `CMUX_SOCKET_PASSWORD`
set), so start the dashboard from a cmux tab if you want Restart and Go to to drive cmux.
Restart and Start fall back to copying the command if no launcher works.

## How sessions are found

| Agent | Process | Session | Resume |
|---|---|---|---|
| Claude Code | `claude` | `<config>/sessions/<pid>.json`, a per-process registry, matched by start time | `claude … --resume <id>` |
| Codex | `codex` (interactive, `resume` and `fork` only) | `codex resume <id>` in argv, else cmux's record, else the newest rollout in `~/.codex/sessions` written in the same folder since the process started | `codex resume … <id>` |
| Gemini | `gemini`, or `node …/gemini` | `--session-id` / `--session-file` in argv, else the newest chat in `~/.gemini/tmp/<project>/chats` written since the process started | `gemini … --session-file <chat>` (Gemini resumes by index, not id) |

Codex and Gemini keep no per-process registry, so with several sessions in one folder the
match is a best guess, and the UI says so. Claude Code config dirs are `~/.claude`, every
`~/.claude-*` with a `sessions/` folder, and any `CLAUDE_CONFIG_DIR` seen on a running process.
`CODEX_HOME` and `GEMINI_CLI_HOME` are honoured.

Process details (exact argv, environment, working folder, start time) come from
[psutil](https://github.com/giampaolo/psutil), which reads argv as a list from the OS rather than
re-splitting `ps` output, so arguments containing spaces or JSON survive intact.

## Safety

- The server binds to `127.0.0.1`.
- Requests whose `Host` header is not localhost are refused, which blocks DNS rebinding.
- Every POST must carry an `X-Agent-Monitor` header, which a cross-site page cannot send.
- Before any signal, the PID's start time and command line are re-checked, so a recycled PID is
  never touched.
- Commands are built server-side from a session's own argv. The browser never supplies one.
- Env vars whose names look secret (`*KEY*`, `*TOKEN*`, …) are never written into a command. The
  UI lists them by name so you know the relaunch needs them.

## Development

```bash
just test
just lint
uv run python scripts/make_icons.py   # regenerate the favicons from the sprite
```

CI runs the tests on macOS, Linux and Windows.

## License

MIT. See [LICENSE](LICENSE).
