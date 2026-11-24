"""Incremental reader for a Claude Code session transcript (<config>/projects/<dir>/<id>.jsonl).

Transcripts reach tens of MB and are polled every few seconds for every session, so each reader
keeps a byte offset and only parses lines appended since the last poll.

Session-only cron jobs live in the Claude process's memory and are never written to
.claude/scheduled_tasks.json, so they are rebuilt here from CronCreate / CronDelete tool calls.
"""

from __future__ import annotations

import json
import os
from dataclasses import dataclass, field
from datetime import datetime

SESSION_CRON_TTL_S = 7 * 86400  # fallback when a CronCreate result has no maxAgeMs (older versions)


def parse_ts(value) -> float | None:
    if not isinstance(value, str):
        return None
    try:
        return datetime.fromisoformat(value).timestamp()
    except ValueError:
        return None


def _text_of(content) -> str:
    if isinstance(content, str):
        return content
    if isinstance(content, list):
        for c in content:
            if isinstance(c, dict) and c.get("type") == "text":
                return c.get("text") or ""
    return ""


@dataclass
class CronJob:
    id: str
    schedule: str
    prompt: str
    recurring: bool
    durable: bool
    created_at: float | None
    next_fire_at: float | None
    max_age_s: float = SESSION_CRON_TTL_S

    def expires_at(self) -> float | None:
        if self.durable:
            return None
        if self.recurring:
            return self.created_at + self.max_age_s if self.created_at else None
        return self.next_fire_at


@dataclass
class Wakeup:
    fire_at: float
    reason: str
    prompt: str


@dataclass
class TranscriptState:
    last_input_at: float | None = None
    last_input_text: str = ""
    last_output_at: float | None = None
    last_event_at: float | None = None
    title: str = ""
    crons: dict[str, CronJob] = field(default_factory=dict)
    deleted: set[str] = field(default_factory=set)
    wakeup: Wakeup | None = None
    pending: dict[str, tuple[str, dict, float | None]] = field(default_factory=dict)


class TranscriptReader:
    """Tails a JSONL file, folding each new record into a state object.

    `factory` makes an empty state and `apply(state, record)` folds one record in; the defaults
    read Claude Code transcripts, and other agents pass their own.
    """

    def __init__(self, path: str, factory=None, apply=None):
        self.path = path
        self.offset = 0
        self.inode = None
        self.factory = factory or TranscriptState
        self.apply = apply or apply_record
        self.state = self.factory()

    def refresh(self):
        try:
            st = os.stat(self.path)
        except OSError:
            return self.state
        if st.st_ino != self.inode or st.st_size < self.offset:
            self.inode, self.offset, self.state = st.st_ino, 0, self.factory()
        if st.st_size == self.offset:
            return self.state
        with open(self.path, "rb") as fh:
            fh.seek(self.offset)
            chunk = fh.read()
        end = chunk.rfind(b"\n")
        if end < 0:
            return self.state
        for line in chunk[: end + 1].splitlines():
            self.feed_line(line)
        self.offset += end + 1
        return self.state

    def feed_line(self, line: bytes) -> None:
        try:
            rec = json.loads(line)
        except (ValueError, UnicodeDecodeError):
            return
        if isinstance(rec, dict):
            self.apply(self.state, rec)


def apply_record(s: TranscriptState, rec: dict) -> None:
    kind = rec.get("type")
    if kind == "ai-title" and rec.get("aiTitle"):
        s.title = rec["aiTitle"]
        return
    ts = parse_ts(rec.get("timestamp"))
    msg = rec.get("message") if isinstance(rec.get("message"), dict) else None
    if ts and kind in ("user", "assistant"):
        s.last_event_at = max(ts, s.last_event_at or 0)
    if kind == "assistant" and msg and not rec.get("isSidechain"):
        if ts:
            s.last_output_at = max(ts, s.last_output_at or 0)
        for c in msg.get("content") or []:
            if isinstance(c, dict) and c.get("type") == "tool_use":
                s.pending[c.get("id", "")] = (c.get("name", ""), c.get("input") or {}, ts)
                _on_tool_use(s, c.get("name", ""), c.get("input") or {}, ts)
        return
    if kind == "user" and msg and not rec.get("isSidechain"):
        content = msg.get("content")
        if isinstance(content, list):
            for c in content:
                if isinstance(c, dict) and c.get("type") == "tool_result":
                    _on_tool_result(
                        s,
                        c.get("tool_use_id", ""),
                        rec.get("toolUseResult"),
                        _text_of(c.get("content")),
                        ts,
                    )
        origin = rec.get("origin") if isinstance(rec.get("origin"), dict) else {}
        synthetic = (
            rec.get("isMeta") or rec.get("isCompactSummary") or rec.get("isVisibleInTranscriptOnly")
        )
        is_human = not synthetic and (
            origin.get("kind") == "human" or (not origin.get("kind") and isinstance(content, str))
        )
        if is_human and ts and (ts >= (s.last_input_at or 0)):
            s.last_input_at = ts
            s.last_input_text = _text_of(content)[:400]


def _on_tool_use(s: TranscriptState, name: str, inp: dict, ts: float | None) -> None:
    if name == "CronDelete" and inp.get("id"):
        s.deleted.add(str(inp["id"]))
    elif name == "ScheduleWakeup":
        if inp.get("stop"):
            s.wakeup = None
        elif ts and inp.get("delaySeconds"):
            delay = min(max(float(inp["delaySeconds"]), 60), 3600)
            s.wakeup = Wakeup(
                ts + delay, str(inp.get("reason") or ""), str(inp.get("prompt") or "")
            )


def _on_tool_result(
    s: TranscriptState, tool_use_id: str, result, text: str, ts: float | None
) -> None:
    use = s.pending.pop(tool_use_id, None)
    if not use or use[0] != "CronCreate":
        return
    _, inp, use_ts = use
    res = result if isinstance(result, dict) else {}
    job_id = res.get("id") or _id_from_text(text)
    if not job_id:
        return
    nfa = res.get("nextFireAt")
    max_age = res.get("maxAgeMs")
    s.crons[str(job_id)] = CronJob(
        max_age_s=float(max_age) / 1000
        if isinstance(max_age, (int, float))
        else SESSION_CRON_TTL_S,
        id=str(job_id),
        schedule=str(res.get("humanSchedule") or inp.get("cron") or ""),
        prompt=str(inp.get("prompt") or ""),
        recurring=bool(res.get("recurring", inp.get("recurring", True))),
        durable=bool(res.get("durable", inp.get("durable", False))),
        created_at=use_ts or ts,
        next_fire_at=float(nfa) / 1000 if isinstance(nfa, (int, float)) else None,
    )


def _id_from_text(text: str) -> str | None:
    # "Scheduled recurring job 494f204c (7,37 * * * *). ..."
    words = text.split()
    for i, w in enumerate(words[:-1]):
        if w == "job":
            return words[i + 1].strip(".,:")
    return None


def active_session_crons(
    s: TranscriptState, process_started_at: float, now: float
) -> list[CronJob]:
    """Session-only jobs this process created that have not been deleted or expired.

    Jobs from before the process started belonged to an earlier run of a resumed session and
    died with it. Durable jobs are reported from scheduled_tasks.json instead.
    """
    out = []
    for job in s.crons.values():
        if job.id in s.deleted or job.durable:
            continue
        if job.created_at and job.created_at < process_started_at - 5:
            continue
        exp = job.expires_at()
        if exp and exp < now:
            continue
        out.append(job)
    return out


def active_wakeup(s: TranscriptState, process_started_at: float, now: float) -> Wakeup | None:
    w = s.wakeup
    if not w or w.fire_at < now or w.fire_at < process_started_at:
        return None
    return w
