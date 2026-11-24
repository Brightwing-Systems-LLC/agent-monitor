import json
import struct
import time

from claude_monitor import gather, procinfo
from claude_monitor.procinfo import Proc


def test_parse_procargs2_keeps_args_with_spaces_and_reads_env():
    raw = (
        struct.pack("i", 3)
        + b"/bin/claude\0\0\0\0"
        + b"claude\0--name\0my session\0"
        + b"A=1\0B=x=y\0\0executable_path=/bin/claude\0"
    )
    argv, env = procinfo.parse_procargs2(raw)
    assert argv == ["claude", "--name", "my session"]
    assert env == {"A": "1", "B": "x=y"}


def test_parse_ps_handles_padded_dates_and_spaced_paths():
    out = (
        "  101     1  2048   1:02.50 Sun Nov  8 09:05:01 2026     "
        "/Applications/Rex.app/Contents/MacOS/Rex Server\n"
    )
    p = procinfo.parse_ps(out)[101]
    assert p.lstart == "Sun Nov 8 09:05:01 2026"
    assert p.cpu_seconds == 62.5
    assert p.comm.endswith("Rex Server")


def test_is_claude_cli():
    assert procinfo.is_claude_cli("claude")
    assert procinfo.is_claude_cli("/Users/u/.local/bin/claude")
    assert procinfo.is_claude_cli("/Users/u/.local/share/claude/versions/2.1.331")
    assert not procinfo.is_claude_cli("/Applications/Claude.app/Contents/MacOS/Claude")


def _table(*rows):
    return {pid: Proc(pid, ppid, 0, 0, "", comm) for pid, ppid, comm in rows}


def test_host_app_named_and_bundle_fallback():
    t = _table((10, 9, "claude"), (9, 8, "/bin/zsh"), (8, 1, "/Users/u/.local/bin/hc"))
    assert procinfo.host_app(10, t) == "herdr"
    t = _table(
        (10, 9, "claude"),
        (9, 8, "/usr/bin/login"),
        (8, 1, "/Applications/Rex.app/Contents/Versions/Current/Rex Server.app/x/rex"),
    )
    assert procinfo.host_app(10, t) == "Rex"


def test_registry_matches_utc_proc_start_against_local_lstart():
    epoch = 1_795_526_605
    local = time.strftime("%a %b %d %H:%M:%S %Y", time.localtime(epoch))
    utc = time.strftime("%a %b %d %H:%M:%S %Y", time.gmtime(epoch))
    proc = Proc(1, 1, 0, 0, procinfo.normalise_lstart(local), "claude")
    assert gather.registry_matches({"procStart": utc}, proc)
    later = time.strftime("%a %b %d %H:%M:%S %Y", time.gmtime(epoch + 3600))
    assert not gather.registry_matches({"procStart": later}, proc)
    assert gather.registry_matches({"startedAt": (epoch + 3) * 1000}, proc)


def test_read_durable_tasks_list_and_object_forms(tmp_path):
    (tmp_path / ".claude").mkdir()
    f = tmp_path / ".claude" / "scheduled_tasks.json"
    f.write_text(json.dumps([{"id": "a1", "cron": "0 9 * * *", "prompt": "hi"}]))
    assert gather.read_durable_tasks(str(tmp_path))[0]["schedule"] == "0 9 * * *"
    f.write_text(json.dumps({"tasks": [{"id": "b2", "humanSchedule": "daily", "prompt": "p"}]}))
    assert gather.read_durable_tasks(str(tmp_path))[0]["id"] == "b2"
    f.write_text("not json")
    assert gather.read_durable_tasks(str(tmp_path)) == []
