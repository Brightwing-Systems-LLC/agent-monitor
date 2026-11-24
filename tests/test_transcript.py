import json
from datetime import UTC

from claude_monitor.transcript import (
    SESSION_CRON_TTL_S,
    TranscriptReader,
    active_session_crons,
    active_wakeup,
)

T0 = 1_800_000_000.0


def iso(t):
    from datetime import datetime

    return datetime.fromtimestamp(t, UTC).isoformat().replace("+00:00", "Z")


def assistant(t, *content):
    return {
        "type": "assistant",
        "timestamp": iso(t),
        "message": {"role": "assistant", "content": list(content)},
    }


def tool_use(uid, name, inp):
    return {"type": "tool_use", "id": uid, "name": name, "input": inp}


def tool_result(t, uid, text, result=None):
    rec = {
        "type": "user",
        "timestamp": iso(t),
        "message": {
            "role": "user",
            "content": [{"type": "tool_result", "tool_use_id": uid, "content": text}],
        },
    }
    if result is not None:
        rec["toolUseResult"] = result
    return rec


def human(t, text, kind="human"):
    return {
        "type": "user",
        "timestamp": iso(t),
        "origin": {"kind": kind},
        "message": {"role": "user", "content": text},
    }


def write(path, records, tail=b""):
    with open(path, "ab") as fh:
        fh.writelines(json.dumps(r).encode() + b"\n" for r in records)
        fh.write(tail)


def test_activity_title_and_human_input(tmp_path):
    p = tmp_path / "t.jsonl"
    write(
        p,
        [
            {"type": "ai-title", "aiTitle": "Fix the thing"},
            human(T0, "please fix it"),
            assistant(T0 + 5, {"type": "text", "text": "ok"}),
            human(T0 + 10, "<task-notification>", kind="task-notification"),
        ],
    )
    s = TranscriptReader(str(p)).refresh()
    assert s.title == "Fix the thing"
    assert s.last_input_at == T0 and s.last_input_text == "please fix it"
    assert s.last_output_at == T0 + 5
    assert s.last_event_at == T0 + 10


def test_reads_incrementally_and_waits_for_a_complete_line(tmp_path):
    p = tmp_path / "t.jsonl"
    partial = json.dumps(human(T0 + 20, "second")).encode()
    write(p, [human(T0, "first")], tail=partial[:10])
    reader = TranscriptReader(str(p))
    assert reader.refresh().last_input_text == "first"
    with open(p, "ab") as fh:
        fh.write(partial[10:] + b"\n")
    assert reader.refresh().last_input_text == "second"


def test_session_crons_created_deleted_and_expired(tmp_path):
    p = tmp_path / "t.jsonl"
    write(
        p,
        [
            assistant(
                T0, tool_use("u1", "CronCreate", {"cron": "7,37 * * * *", "prompt": "check"})
            ),
            tool_result(
                T0 + 1,
                "u1",
                "Scheduled recurring job aaaa1111 (7,37 * * * *).",
                {
                    "id": "aaaa1111",
                    "humanSchedule": "7,37 * * * *",
                    "recurring": True,
                    "durable": False,
                    "nextFireAt": (T0 + 600) * 1000,
                },
            ),
            assistant(
                T0 + 2, tool_use("u2", "CronCreate", {"cron": "0 9 * * *", "prompt": "daily"})
            ),
            tool_result(
                T0 + 3,
                "u2",
                "Scheduled recurring job bbbb2222 (Every day at 9:00 AM). Session-only",
            ),
            assistant(T0 + 4, tool_use("u3", "CronDelete", {"id": "bbbb2222"})),
            assistant(
                T0 + 5,
                tool_use("u4", "CronCreate", {"cron": "x", "prompt": "durable", "durable": True}),
            ),
            tool_result(T0 + 6, "u4", "", {"id": "cccc3333", "recurring": True, "durable": True}),
        ],
    )
    s = TranscriptReader(str(p)).refresh()
    jobs = active_session_crons(s, process_started_at=T0 - 60, now=T0 + 100)
    assert [j.id for j in jobs] == ["aaaa1111"]
    assert jobs[0].schedule == "7,37 * * * *"
    assert active_session_crons(s, T0 - 60, now=T0 + SESSION_CRON_TTL_S + 10) == []


def test_crons_from_an_earlier_run_of_a_resumed_session_are_ignored(tmp_path):
    p = tmp_path / "t.jsonl"
    write(
        p,
        [
            assistant(T0, tool_use("u1", "CronCreate", {"cron": "* * * * *", "prompt": "p"})),
            tool_result(T0 + 1, "u1", "Scheduled recurring job dddd4444 (* * * * *)."),
        ],
    )
    s = TranscriptReader(str(p)).refresh()
    assert active_session_crons(s, process_started_at=T0 + 3600, now=T0 + 3700) == []


def test_loop_wakeup_pending_then_stopped(tmp_path):
    p = tmp_path / "t.jsonl"
    write(
        p,
        [
            assistant(
                T0,
                tool_use(
                    "w1",
                    "ScheduleWakeup",
                    {"delaySeconds": 1200, "reason": "watch CI", "prompt": "/loop x"},
                ),
            )
        ],
    )
    reader = TranscriptReader(str(p))
    w = active_wakeup(reader.refresh(), T0 - 10, now=T0 + 60)
    assert w and w.fire_at == T0 + 1200 and w.reason == "watch CI"
    assert active_wakeup(reader.state, T0 - 10, now=T0 + 1300) is None
    write(p, [assistant(T0 + 100, tool_use("w2", "ScheduleWakeup", {"stop": True}))])
    assert active_wakeup(reader.refresh(), T0 - 10, now=T0 + 200) is None


def test_compaction_summary_is_not_counted_as_input(tmp_path):
    p = tmp_path / "t.jsonl"
    summary = human(T0 + 50, "This session is being continued from a previous conversation")
    summary.pop("origin")
    summary |= {"isCompactSummary": True, "isVisibleInTranscriptOnly": True}
    write(p, [human(T0, "real prompt"), summary])
    s = TranscriptReader(str(p)).refresh()
    assert s.last_input_text == "real prompt" and s.last_input_at == T0


def test_cron_expiry_uses_max_age_from_the_result(tmp_path):
    p = tmp_path / "t.jsonl"
    write(
        p,
        [
            assistant(T0, tool_use("u1", "CronCreate", {"cron": "17 * * * *", "prompt": "p"})),
            tool_result(
                T0 + 1,
                "u1",
                "",
                {"id": "e5", "recurring": True, "durable": False, "maxAgeMs": 30 * 86400 * 1000},
            ),
        ],
    )
    s = TranscriptReader(str(p)).refresh()
    assert [j.id for j in active_session_crons(s, T0 - 5, now=T0 + 20 * 86400)] == ["e5"]
    assert active_session_crons(s, T0 - 5, now=T0 + 31 * 86400) == []
