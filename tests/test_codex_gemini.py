import json
import shlex
from datetime import UTC

from agent_monitor.agents import base, codex, gemini
from agent_monitor.agents.base import Caches, Candidate
from agent_monitor.commands import POSIX
from agent_monitor.procinfo import Proc
from agent_monitor.transcript import TranscriptReader

T0 = 1_800_000_000.0


def iso(t):
    from datetime import datetime

    return datetime.fromtimestamp(t, UTC).isoformat().replace("+00:00", "Z")


# --- matching sessions by time -------------------------------------------------------------


def test_assign_prefers_newest_written_session_created_by_the_process():
    files = [
        {"path": "a", "started": T0 + 1, "updated": T0 + 100},  # first session
        {"path": "b", "started": T0 + 4000, "updated": T0 + 9000},  # after /new, still active
    ]
    got = base.assign_by_time([(1, T0)], files)
    assert got[1]["path"] == "b" and got[1]["match"] == "new in this process"


def test_assign_resumed_session_and_two_processes_in_one_folder():
    files = [
        {"path": "old", "started": T0 - 86400, "updated": T0 + 50},  # resumed by proc 1
        {"path": "new", "started": T0 + 500, "updated": T0 + 600},  # made by proc 2
    ]
    got = base.assign_by_time([(1, T0), (2, T0 + 499)], files)
    assert got[2]["path"] == "new"
    assert got[1]["path"] == "old" and got[1]["match"] == "resumed (matched by time)"


def test_assign_ignores_files_untouched_since_the_process_started():
    assert (
        base.assign_by_time([(1, T0)], [{"path": "x", "started": T0 - 999, "updated": T0 - 500}])
        == {}
    )


# --- codex ----------------------------------------------------------------------------------


def test_codex_argv_parsing_and_interactive_detection():
    agent = codex.Codex()
    proc = Proc(1, 0, "/usr/bin/codex", T0, 0, 0)
    assert agent.matches(proc, ["codex", "--yolo"])
    assert agent.matches(proc, ["codex", "-c", "model=o3", "resume", "abc"])
    assert not agent.matches(proc, ["codex", "-c", "x=1", "app-server", "--analytics"])
    assert not agent.matches(proc, ["codex", "exec", "do it"])
    assert not agent.matches(Proc(1, 0, "x", T0, 0, 0), ["codex-code-mode-host", "--listen"])
    opts, sub, pos = codex.parse_argv(["codex", "--disable", "computer_use", "resume", "--last"])
    assert opts == ["--disable", "computer_use", "--last"] and sub == "resume" and pos == []


def test_codex_resume_command_keeps_options_and_drops_prompt():
    argv = [
        "/home/u/.codex/packages/standalone/releases/0.1/bin/codex",
        "-c",
        'mcp.cmd="/a b"',
        "--yolo",
        "fix the bug",
    ]
    cmd = codex.build_command(
        argv, {"CODEX_HOME": "/h", "OPENAI_API_KEY": "sk"}, "/w", "sid", POSIX
    )
    assert shlex.split(cmd["resume"]) == [
        "CODEX_HOME=/h",
        "codex",
        "resume",
        "-c",
        'mcp.cmd="/a b"',
        "--yolo",
        "sid",
    ]
    assert cmd["dropped_prompt"] == "fix the bug" and cmd["secret_env"] == ["OPENAI_API_KEY"]


def test_codex_rollout_state(tmp_path):
    p = tmp_path / "rollout.jsonl"
    recs = [
        {
            "timestamp": iso(T0),
            "type": "session_meta",
            "payload": {"id": "s1", "cwd": "/w", "timestamp": iso(T0), "cli_version": "0.171.0"},
        },
        {"timestamp": iso(T0 + 1), "type": "event_msg", "payload": {"type": "task_started"}},
        {
            "timestamp": iso(T0 + 1),
            "type": "response_item",
            "payload": {
                "type": "message",
                "role": "user",
                "content": [{"type": "input_text", "text": "<environment_context>...</>"}],
            },
        },
        {
            "timestamp": iso(T0 + 2),
            "type": "response_item",
            "payload": {
                "type": "message",
                "role": "user",
                "content": [{"type": "input_text", "text": "make it faster"}],
            },
        },
        {
            "timestamp": iso(T0 + 9),
            "type": "response_item",
            "payload": {
                "type": "message",
                "role": "assistant",
                "content": [{"type": "output_text", "text": "done"}],
            },
        },
    ]
    p.write_text("".join(json.dumps(r) + "\n" for r in recs))
    s = TranscriptReader(str(p), codex.CodexState, codex.apply_record).refresh()
    assert s.busy is True and s.version == "0.171.0"
    assert s.last_input_text == "make it faster" and s.last_input_at == T0 + 2
    assert s.last_output_at == T0 + 9
    with open(p, "a") as fh:
        fh.write(
            json.dumps(
                {
                    "timestamp": iso(T0 + 10),
                    "type": "event_msg",
                    "payload": {"type": "task_complete"},
                }
            )
            + "\n"
        )
    assert TranscriptReader(str(p), codex.CodexState, codex.apply_record).refresh().busy is False
    assert codex._session_meta(str(p)) == {"id": "s1", "cwd": "/w", "started": T0}


# --- gemini ---------------------------------------------------------------------------------


def test_gemini_process_detection():
    agent = gemini.Gemini()
    node = Proc(1, 0, "/usr/bin/node", T0, 0, 0)
    assert agent.matches(node, ["node", "/home/u/.nvm/bin/gemini", "--yolo"])
    assert agent.matches(node, ["node", "/x/node_modules/@google/gemini-cli/dist/index.js"])
    assert agent.matches(node, ["node", "/x/bin/gemini", "-i", "start here"])
    assert not agent.matches(node, ["node", "/x/bin/gemini", "-p", "one shot"])
    assert not agent.matches(node, ["node", "/x/bin/gemini", "mcp", "list"])
    assert not agent.matches(node, ["node", "/x/server.js"])


def test_gemini_command_uses_session_file_and_drops_session_flags():
    argv = ["/usr/bin/gemini", "--yolo", "--resume", "latest", "-m", "gemini-3-pro"]
    cmd = gemini.build_command(
        argv,
        {"GEMINI_API_KEY": "k", "GOOGLE_CLOUD_PROJECT": "p"},
        "/w",
        "/h/chats/session-1.json",
        POSIX,
    )
    words = shlex.split(cmd["resume"])
    assert words[0] == "GOOGLE_CLOUD_PROJECT=p"
    assert words[2:] == [
        "--yolo",
        "-m",
        "gemini-3-pro",
        "--session-file",
        "/h/chats/session-1.json",
    ]
    assert cmd["secret_env"] == ["GEMINI_API_KEY"]


def _chat(path, sid, start, updated, user="hello there", reply=True):
    msgs = [{"id": "1", "timestamp": iso(start + 1), "type": "user", "content": [{"text": user}]}]
    if reply:
        msgs.append({"id": "2", "timestamp": iso(start + 2), "type": "gemini", "content": "hi"})
    path.write_text(
        json.dumps(
            {
                "sessionId": sid,
                "startTime": iso(start),
                "lastUpdated": iso(updated),
                "messages": msgs,
            }
        )
    )


def test_gemini_sessions_match_chats_and_skip_relaunch_parent(tmp_path, monkeypatch):
    home = tmp_path / ".gemini"
    chats = home / "tmp" / "proj" / "chats"
    chats.mkdir(parents=True)
    (home / "projects.json").write_text(json.dumps({"projects": {"/w": "proj"}}))
    _chat(chats / "session-a.json", "sid-a", T0 + 2, T0 + 60, user="fix login\nmore")
    monkeypatch.setattr(gemini, "HOME", tmp_path)
    parent = Proc(10, 1, "/usr/bin/node", T0, 0, 0)
    child = Proc(11, 10, "/usr/bin/node", T0 + 1, 0, 0)
    table = {10: parent, 11: child}
    argv = ["node", "/x/bin/gemini"]
    cands = [Candidate(p, argv, table, None, {}, "/w") for p in (parent, child)]
    out = gemini.Gemini().sessions(cands, Caches(), T0 + 100)
    assert [s["pid"] for s in out] == [11]
    s = out[0]
    assert s["session_id"] == "sid-a" and s["title"] == "fix login"
    assert s["last_input_at"] == T0 + 3 and s["last_output_at"] == T0 + 4
    assert s["command"]["resumable"] and "--session-file" in s["command"]["resume"]


def test_gemini_chats_dir_falls_back_to_sha256(tmp_path):
    import hashlib

    d = gemini.chats_dir(tmp_path, "/some/where", {})
    assert d.parent.name == hashlib.sha256(b"/some/where").hexdigest()
