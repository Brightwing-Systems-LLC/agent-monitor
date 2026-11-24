# agent-monitor

A local web dashboard of every coding-agent session running on your machine (**Claude Code**,
**OpenAI Codex CLI** and **Google Gemini CLI**), showing what each one is doing, with buttons to
stop, restart, resume or jump to it.

> **Unofficial.** agent-monitor is an independent project. It is not made, endorsed or
> supported by Anthropic, OpenAI or Google. "Claude", "Claude Code" and the Claude Code mascot
> are Anthropic's trademarks and artwork; "Codex" and "Gemini" are trademarks of OpenAI and
> Google. They are used here only to identify the sessions being monitored.

## Why

Once you run more than a few agents at a time, across terminals, tmux panes, cmux workspaces
and editor windows, it gets hard to answer simple questions:

- Which sessions are still running, and which of them are working right now?
- Which ones have sat untouched for days, holding memory and file watchers?
- Does this session have a scheduled job that dies if I close it?
- How do I reopen that session exactly as it was, with the same flags, env and folder?

agent-monitor answers those on one page that refreshes every five seconds. It reads only what
is already on your machine: the process table and the agents' own session files. Nothing is
sent anywhere.

## Quick start

You need **Python 3.11+** and **[uv](https://docs.astral.sh/uv/)**. It runs on **macOS, Linux and
Windows**.

Run it without installing:

```bash
uvx --from git+https://github.com/Brightwing-Systems-LLC/agent-monitor agent-monitor
```

Then open **http://localhost:8447**.

## Setup

### Install it as a command

```bash
uv tool install git+https://github.com/Brightwing-Systems-LLC/agent-monitor
agent-monitor
```

Upgrade later with `uv tool upgrade agent-monitor`.

### Or run it from a clone

```bash
git clone https://github.com/Brightwing-Systems-LLC/agent-monitor
cd agent-monitor
uv run agent-monitor        # or `just dev` if you have just
```

### Where to start it

**Start it from your own terminal, not from inside an agent.** If an agent starts the server
(for example, as a background shell task), the server is that agent's child process. When the
agent exits or you stop it, the dashboard goes down with it. If that happens, the page shows
an offline banner, and the session that started the dashboard is labelled **runs this
monitor**.

On macOS with cmux, start it from a cmux tab. cmux only accepts commands from processes
running inside it, and the **go to** and **restart** buttons need that (see
[Relaunching](#relaunching)).

### Configuration

All settings are environment variables, and none are required.

| Variable | Default | What it does |
|---|---|---|
| `AGENT_MONITOR_PORT` | `8447` | Port for the dashboard. |
| `AGENT_MONITOR_STATE` | `~/.local/state/agent-monitor` (`$XDG_STATE_HOME` if set; `%LOCALAPPDATA%\agent-monitor` on Windows) | Where the **Stopped** list is saved. |
| `CLAUDE_CONFIG_DIR`, `CODEX_HOME`, `GEMINI_CLI_HOME` | the agents' own defaults | Read from each running agent's environment, so the monitor finds sessions in non-default config folders on its own. |

## Using it

### The session cards

Each running agent process gets a card:

- **Mascot, title and name.** Each agent has its own pixel mascot: a crab for Claude Code, a
  terminal robot for Codex, a sparkle for Gemini. Every session wears a costume tied to its
  session id, so you can spot the same session again at a glance. It walks while the agent is
  busy and dozes once the session has been quiet for a day. Browse all the costumes at
  `/#gallery` (the **mascots** link).
- **Status chips.** **busy** or **idle**, the host app, the config folder when it isn't the
  default, the number of scheduled jobs, how long the session has been quiet, and warnings.
- **Folder.** The working directory the agent was started in.
- **Timing.** When the session started, when you last typed into it (the prompt is shown
  underneath), when it last replied, and its CPU time and memory. Anything older than 24 hours
  is highlighted.
- **Scheduled jobs (Claude Code only).**
  - Session-only cron jobs created with `CronCreate`. These live only in that process's memory,
    so stopping the session loses them. The monitor rebuilds them from the transcript.
  - A pending `/loop` wakeup.
  - Durable tasks from `<project>/.claude/scheduled_tasks.json`, and whether this session holds
    the lock that runs them.
- **command** (expand it):
  - **resume**: a command that changes to the session's folder and reopens the same
    conversation with the same flags and env, written for your shell (POSIX or PowerShell);
  - **original**: the exact command line the process was started with;
  - **session**: the session id.
- **remote control ↗**: the session's Remote Control link, when it has one (Claude Code).

The cards also carry these warnings:

| Warning | Meaning |
|---|---|
| same session also open in pid … | Two processes are writing to one conversation, which can interleave its history. |
| runs this monitor | This session started the dashboard; stopping it stops the page too. |
| child of another agent | The agent was started by another agent (a subagent or wrapper). |
| session matched by folder and timing | Codex/Gemini only: check the session id before relying on the resume command (see [How it works](#how-it-works)). |
| secret env not repeated | Env vars whose names look secret (`*KEY*`, `*TOKEN*`, …) are left out of the resume command. The names are listed so you know to set them. |

### Filtering and sorting

The toolbar filters by text (matching name, folder, prompt, host app or PID), by agent and by
host app. It sorts by last activity, start time, CPU time or memory. **quiet > 24h only** shows
sessions with no activity for a day and no scheduled jobs, which are usually safe to stop.

### Actions

Buttons that stop a session ask you to confirm with a second click within five seconds.

| Button | What it does |
|---|---|
| **go to** | Focuses the session's cmux workspace and tab (cmux sessions only). |
| **copy resume cmd** | Copies the resume command to the clipboard. |
| **restart** | Stops the session, waits up to 15 s for it to exit, then relaunches it with the resume command. If it does not exit in time, nothing is relaunched. |
| **stop** | Sends SIGTERM. The agent shuts down cleanly. Claude Code, for example, closes Remote Control and runs its SessionEnd hooks. |
| **force** | Sends SIGKILL. The process ends at once with no cleanup. Use it when stop reports "has not exited yet". |
| **select shown** / **stop selected** | Tick cards (or tick everything the current filter shows), then stop them all in one go. |

Windows has no clean-shutdown signal for a process in another console, so stop there ends the
process immediately and the force button is not shown.

Before stopping anything, the page warns you if the session is busy, if it started the
dashboard, or if it has session-only scheduled jobs that will be lost.

### Stopped / exited

Below the running sessions is a list of sessions you stopped from the dashboard, plus Claude Code
sessions whose process died without cleaning up. Each row has **start** (relaunch it),
**copy cmd** and **dismiss**. The list is kept on disk, so it survives restarting the monitor.

### Relaunching

**restart** and **start** open the session in the first launcher that works, trying the
session's own host app first:

| OS | Launchers, in order |
|---|---|
| macOS | cmux workspace, herdr terminal, Terminal.app |
| Linux | herdr terminal, tmux window (when a tmux server is running), GNOME Terminal / Konsole / kitty / WezTerm / Alacritty / xterm |
| Windows | Windows Terminal tab, a new PowerShell console |

If no launcher works, the resume command is copied to your clipboard instead.

## How it works

agent-monitor is a small Flask server with a single-page UI. Every five seconds the page asks
the server for a snapshot, and the server builds it like this:

1. **Read the process table.** [psutil](https://github.com/giampaolo/psutil) lists every process
   with its argv, environment, working folder, start time, CPU and memory. argv comes from the OS
   as a list rather than re-split `ps` output, so arguments containing spaces or JSON survive.
2. **Let each agent adapter claim its processes.** Each agent has an adapter
   (`agent_monitor/agents/`) that recognises that agent's processes, including ones launched
   through wrappers such as a Node shim.
3. **Tie each process to its session** using the agent's own files:

   | Agent | Session found from | Resumed with |
   |---|---|---|
   | Claude Code | `<config>/sessions/<pid>.json`, a per-process registry, matched by start time | `claude … --resume <id>` |
   | Codex | `codex resume <id>` in argv, else cmux's record, else the newest rollout in `~/.codex/sessions` written in the same folder since the process started | `codex resume … <id>` |
   | Gemini | `--session-id` / `--session-file` in argv, else the newest chat in `~/.gemini/tmp/<project>/chats` written since the process started | `gemini … --session-file <chat>` |

   Codex and Gemini keep no per-process registry. With several sessions in one folder, the
   match is a best guess, and the card says so. Claude Code config dirs are `~/.claude`, every
   `~/.claude-*` with a `sessions/` folder, and any `CLAUDE_CONFIG_DIR` seen on a running
   process.
4. **Read the transcript** for the last prompt and reply, busy/idle state and scheduled jobs.
5. **Find the host app** by walking up the process's parents: cmux, herdr, tmux, Windows
   Terminal, VS Code, Orca, or any other macOS `.app`.
6. **Build the resume command** from the process's own argv and env: replace any earlier
   resume flag with this session's id, drop the initial prompt, keep every other flag and the
   agent's own config env (model, permission mode, MCP config…), leave out secrets, and quote it
   for your shell.

### Safety

The dashboard can stop processes, so it is locked down:

- The server binds to `127.0.0.1` only.
- Requests whose `Host` header is not localhost are refused, which blocks DNS rebinding.
- Every POST must carry an `X-Agent-Monitor` header, which a cross-site page cannot send.
- Before any signal, the PID's start time and command line are checked again, so a reused PID
  is never touched.
- Commands are built on the server from a session's own argv. The browser never supplies a
  command.
- Secret-looking env vars are never written into a command.

## Development

```bash
git clone https://github.com/Brightwing-Systems-LLC/agent-monitor
cd agent-monitor
uv sync
just dev        # run the dashboard
just test       # pytest
just lint       # ruff check + format check
just fmt        # apply ruff fixes and formatting
uv run python scripts/make_icons.py   # regenerate the favicons
```

CI runs the tests and lint on macOS, Linux and Windows.

Code layout:

| Path | Role |
|---|---|
| `agent_monitor/procinfo.py` | psutil snapshot: argv, env, cwd, host app |
| `agent_monitor/agents/` | one adapter per agent; `base.py` holds the shared session shape |
| `agent_monitor/gather.py` | hands each process to the adapter that claims it |
| `agent_monitor/transcript.py` | prompts, replies, busy state and scheduled jobs from transcripts |
| `agent_monitor/commands.py` | renders resume commands for POSIX shells and PowerShell |
| `agent_monitor/actions.py` | stop, relaunch per OS, focus, the Stopped store |
| `agent_monitor/web.py` | the API and its request guards |
| `agent_monitor/static/` | the page (`index.html`) and the mascot sprites (`mascots.js`) |

**Adding an agent** takes two pieces: a new `agent_monitor/agents/<name>.py` that implements
`matches()` (claim a process) and `sessions()` (build its cards), and a body for its mascot in
`agentBody()` in `mascots.js`. `CLAUDE.md` lists the per-agent traps already found, and is worth
reading first.

## License

MIT. See [LICENSE](LICENSE).
