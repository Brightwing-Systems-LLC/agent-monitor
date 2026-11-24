"""Build the dashboard's view of every coding-agent session on this machine.

One pass per poll: snapshot the process table, hand each agent the processes it recognises
(see agents/), and merge what they return. cmux's hook records are read once and shared, since
they map PIDs to workspaces/tabs (and session ids) for every agent cmux runs.
"""

from __future__ import annotations

import copy
import json
import os
import subprocess
import sys
import threading
import time

from . import procinfo
from .agents import AGENTS
from .agents.base import Caches, Candidate

CMUX = "/Applications/cmux.app/Contents/Resources/bin/cmux"
_PREFILTER = ("claude", "codex", "gemini", "node", "bun", "deno")


def _maybe_agent(proc: procinfo.Proc) -> bool:
    """Cheap name check before paying for each process's argv."""
    name = proc.name.lower()
    return any(word in name for word in _PREFILTER)


class Monitor:
    """Holds per-file readers and slow-changing caches between polls."""

    def __init__(self):
        self.caches = Caches()
        self.cmux_cache: tuple[float, dict[int, dict]] = (0.0, {})
        self.lock = threading.Lock()
        self.last: tuple[float, dict | None] = (0.0, None)

    def cmux_records(self) -> dict[int, dict]:
        stamp, cached = self.cmux_cache
        if time.time() - stamp < 10 or not os.path.exists(CMUX):
            return cached
        try:
            out = subprocess.run(
                [CMUX, "sessions", "list", "--json", "--all"],
                capture_output=True,
                text=True,
                timeout=10,
                check=False,
            ).stdout
            rows = json.loads(out).get("sessions") or []
        except (OSError, ValueError, subprocess.TimeoutExpired):
            rows = []
        recs = {}
        for r in rows:
            if r.get("pid") and r.get("agent_lifecycle") != "ended":
                recs.setdefault(int(r["pid"]), r)
        self.cmux_cache = (time.time(), recs)
        return recs

    def gather(self, max_age: float = 2.0) -> dict:
        """A scan costs ~1-2s with dozens of sessions; open tabs share one recent result."""
        with self.lock:
            stamp, cached = self.last
            if cached is None or time.time() - stamp > max_age:
                cached = self._gather()
                self.last = (time.time(), cached)
            return copy.deepcopy(cached)

    def _gather(self) -> dict:
        now = time.time()
        table = procinfo.snapshot()
        cmux = self.cmux_records()
        claimed: dict[str, list[Candidate]] = {a.key: [] for a in AGENTS}
        for pid, proc in table.items():
            if not _maybe_agent(proc):
                continue
            argv = procinfo.cmdline(pid)
            for agent in AGENTS:
                if agent.matches(proc, argv):
                    claimed[agent.key].append(Candidate(proc, argv, table, cmux.get(pid)))
                    break
        sessions, stale = [], []
        live = set(table)
        for agent in AGENTS:
            sessions += agent.sessions(claimed[agent.key], self.caches, now)
            stale += agent.stale(self.caches, live)
        sessions.sort(key=lambda s: -(s["last_activity_at"] or s["started_at"] or 0))
        return {
            "generated_at": now,
            "sessions": sessions,
            "stale": stale,
            "platform": sys.platform,
            "agents": [{"key": a.key, "label": a.label} for a in AGENTS],
        }
