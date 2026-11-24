import json
import os
import time

from agent_monitor import procinfo
from agent_monitor.agents import claude
from agent_monitor.procinfo import Proc


def test_exe_base_handles_every_platform():
    assert procinfo.exe_base("claude") == "claude"
    assert procinfo.exe_base("/usr/local/bin/codex") == "codex"
    assert procinfo.exe_base(r"C:\Users\me\AppData\Roaming\npm\gemini.CMD") == "gemini.cmd"
    assert procinfo.exe_base(r"C:\tools\Claude.EXE") == "claude"


def test_snapshot_and_identity_of_this_process():
    table = procinfo.snapshot()
    me = table[os.getpid()]
    assert me.create_time > 0
    assert procinfo.same_process(os.getpid(), me.create_time)
    assert not procinfo.same_process(os.getpid(), me.create_time - 100)
    assert procinfo.cwd(os.getpid()) == os.getcwd()
    assert procinfo.cmdline(os.getpid())


def _table(*rows):
    return {pid: Proc(pid, ppid, name, 0.0, 0, 0.0) for pid, ppid, name in rows}


def test_host_app_named_hosts_and_bundle_fallback():
    t = _table((10, 9, "claude"), (9, 8, "/bin/zsh"), (8, 1, "/Users/u/.local/bin/hc"))
    assert procinfo.host_app(10, t) == "herdr"
    t = _table(
        (10, 9, "claude"),
        (9, 8, "/usr/bin/login"),
        (8, 1, "/Applications/Rex.app/Contents/Versions/Current/Rex Server.app/x/rex"),
    )
    assert procinfo.host_app(10, t) == "Rex"
    t = _table(
        (10, 9, r"C:\bin\claude.exe"),
        (9, 8, r"C:\Windows\pwsh.exe"),
        (8, 1, r"C:\Program Files\WindowsApps\Terminal\WindowsTerminal.exe"),
    )
    assert procinfo.host_app(10, t) == "Windows Terminal"
    t = _table((10, 9, "/usr/bin/claude"), (9, 8, "/usr/bin/bash"), (8, 1, "tmux: server"))
    assert procinfo.host_app(10, t) == "tmux"


def test_registry_matches_utc_proc_start_against_create_time():
    epoch = 1_795_526_605.42
    utc = time.strftime("%a %b %d %H:%M:%S %Y", time.gmtime(int(epoch)))
    assert claude.registry_matches({"procStart": utc}, epoch)
    later = time.strftime("%a %b %d %H:%M:%S %Y", time.gmtime(int(epoch) + 3600))
    assert not claude.registry_matches({"procStart": later}, epoch)
    assert claude.registry_matches({"startedAt": (epoch + 3) * 1000}, epoch)


def test_claude_matches_cli_but_not_desktop_app():
    agent = claude.Claude()
    assert agent.matches(
        Proc(1, 0, "/Users/u/.local/share/claude/versions/2.1.3", 0, 0, 0), ["claude", "--yolo"]
    )
    assert agent.matches(
        Proc(1, 0, r"C:\Users\u\.local\bin\claude.exe", 0, 0, 0),
        [r"C:\Users\u\.local\bin\claude.exe"],
    )
    assert not agent.matches(
        Proc(1, 0, "/Applications/Claude.app/Contents/MacOS/Claude", 0, 0, 0),
        ["/Applications/Claude.app/Contents/MacOS/Claude"],
    )


def test_read_durable_tasks_list_and_object_forms(tmp_path):
    (tmp_path / ".claude").mkdir()
    f = tmp_path / ".claude" / "scheduled_tasks.json"
    f.write_text(json.dumps([{"id": "a1", "cron": "0 9 * * *", "prompt": "hi"}]))
    assert claude.read_durable_tasks(str(tmp_path))[0]["schedule"] == "0 9 * * *"
    f.write_text(json.dumps({"tasks": [{"id": "b2", "humanSchedule": "daily", "prompt": "p"}]}))
    assert claude.read_durable_tasks(str(tmp_path))[0]["id"] == "b2"
    f.write_text("not json")
    assert claude.read_durable_tasks(str(tmp_path)) == []
