"""Flask app: one page plus a JSON API polled every few seconds.

Bound to 127.0.0.1. The page exposes launch commands and prompt text and can signal processes,
so two guards apply to every request:
  * the Host header must be localhost / 127.0.0.1 (blocks DNS-rebinding pages from reading it)
  * POSTs must carry X-Claude-Monitor: 1, a custom header a cross-site form or fetch cannot send
    without a CORS preflight, which this app never approves.
"""

from __future__ import annotations

import os
import threading
import time
from pathlib import Path

from flask import Flask, abort, jsonify, request, send_file

from . import actions
from .gather import Monitor

STATIC = Path(__file__).parent / "static"
PORT = int(os.environ.get("CLAUDE_MONITOR_PORT", "8447"))
STALE_MAX_AGE_S = 14 * 86400

app = Flask(__name__)
monitor = Monitor()
stopped = actions.StoppedStore()

_lock = threading.Lock()
_last: dict[int, dict] = {}
_running: dict[str, str] = {}
_recent: list[dict] = []
_seq = 0
_dismissed: set[str] = set()


@app.before_request
def _guard():
    host = (request.host or "").rsplit(":", 1)[0]
    if host not in ("localhost", "127.0.0.1"):
        abort(403)
    if request.method == "POST" and request.headers.get("X-Claude-Monitor") != "1":
        abort(403)


def _toast(target: str, ok: bool, message: str) -> None:
    global _seq
    with _lock:
        _seq += 1
        _recent.insert(
            0, {"id": _seq, "target": target, "ok": ok, "message": message, "at": time.time()}
        )
        del _recent[30:]


def _run(target: str, label: str, fn) -> bool:
    with _lock:
        if target in _running:
            return False
        _running[target] = label

    def work():
        try:
            msg = fn()
            _toast(target, True, msg)
        except actions.ActionError as e:
            _toast(target, False, str(e))
        except Exception as e:  # noqa: BLE001 - surface anything to the page
            _toast(target, False, f"{label} failed: {e}")
        finally:
            with _lock:
                _running.pop(target, None)

    threading.Thread(target=work, daemon=True).start()
    return True


@app.get("/")
def index():
    return send_file(STATIC / "index.html")


@app.get("/favicon.ico")
def favicon():
    return send_file(STATIC / "favicon-32.png", mimetype="image/png", max_age=86400)


@app.get("/apple-touch-icon.png")
def apple_touch_icon():
    return send_file(STATIC / "apple-touch-icon.png", max_age=86400)


@app.get("/api/sessions")
def api_sessions():
    data = monitor.gather()
    live_ids = {s["session_id"] for s in data["sessions"] if s["session_id"]}
    with _lock:
        _last.clear()
        _last.update({s["pid"]: s for s in data["sessions"]})

    live_started = {}
    for s in data["sessions"]:
        if s["session_id"]:
            live_started[s["session_id"]] = max(
                live_started.get(s["session_id"], 0), s["started_at"] or 0
            )
    past = []
    for item in stopped.load():
        sid = item.get("session_id")
        if sid in live_started:
            # Still shutting down: keep the record. Relaunched since the stop: forget it.
            if live_started[sid] > item.get("stopped_at", 0):
                stopped.remove(sid)
            continue
        past.append(item | {"source": "dashboard"})
    seen = {p["session_id"] for p in past}
    cutoff = time.time() - STALE_MAX_AGE_S
    for st in data.pop("stale"):
        sid = st["session_id"]
        if sid in live_ids or sid in seen or sid in _dismissed:
            continue
        if (st.get("updated_at") or 0) < cutoff:
            continue
        seen.add(sid)
        past.append(st | {"source": "exited"})
    past.sort(key=lambda p: -(p.get("stopped_at") or p.get("updated_at") or 0))
    with _lock:
        data["running_actions"] = dict(_running)
        data["toasts"] = list(_recent)
    data["stopped"] = past
    return jsonify(data)


def _session_from_request(pid: int) -> dict:
    body = request.get_json(silent=True) or {}
    with _lock:
        session = _last.get(pid)
    try:
        started = float(body.get("proc_start"))
    except (TypeError, ValueError):
        abort(400, "proc_start required")
    if not session or abs(session["proc_start"] - started) > 0.01:
        abort(409, "session changed; refresh")
    return session


@app.post("/api/session/<int:pid>/stop")
def api_stop(pid: int):
    session = _session_from_request(pid)
    force = bool((request.get_json(silent=True) or {}).get("force"))

    def do():
        actions.stop(pid, session["proc_start"], session["agent"], force=force)
        stopped.add(session)
        if actions.wait_exit(pid, session["proc_start"]):
            return f"stopped {session['name'] or pid}"
        raise actions.ActionError(f"{session['name'] or pid} has not exited yet; Force stop it")

    return _queue(f"pid:{pid}", "force stop" if force else "stop", do)


@app.post("/api/session/<int:pid>/restart")
def api_restart(pid: int):
    session = _session_from_request(pid)
    if not (session["command"] or {}).get("resumable"):
        return jsonify(error="this session cannot be resumed"), 400

    def do():
        actions.stop(pid, session["proc_start"], session["agent"])
        stopped.add(session)
        if not actions.wait_exit(pid, session["proc_start"]):
            raise actions.ActionError(
                f"{session['name'] or pid} did not exit within {actions.STOP_WAIT_S}s; "
                "nothing was relaunched. Force stop it, then Start it from Stopped."
            )
        where = actions.launch(session)
        stopped.remove(session["session_id"])
        return f"restarted {session['name'] or pid} in {where}"

    return _queue(f"pid:{pid}", "restart", do)


@app.post("/api/session/<int:pid>/focus")
def api_focus(pid: int):
    session = _session_from_request(pid)
    try:
        actions.focus(session)
    except actions.ActionError as e:
        return jsonify(error=str(e)), 400
    return jsonify(ok=True)


def _past(session_id: str) -> dict:
    item = stopped.get(session_id)
    if item:
        return item
    for st in monitor.gather()["stale"]:
        if st["session_id"] == session_id:
            return st
    abort(404)


@app.post("/api/stopped/<session_id>/start")
def api_start(session_id: str):
    item = _past(session_id)

    def do():
        where = actions.launch(item)
        stopped.remove(session_id)
        return f"started {item.get('name') or session_id[:8]} in {where}"

    return _queue(f"sid:{session_id}", "start", do)


@app.post("/api/stopped/<session_id>/dismiss")
def api_dismiss(session_id: str):
    stopped.remove(session_id)
    _dismissed.add(session_id)
    return jsonify(ok=True)


def _queue(target: str, label: str, fn):
    if not _run(target, label, fn):
        return jsonify(error="already in progress"), 409
    return jsonify(ok=True)


def main() -> None:
    print(f"claude-monitor: http://localhost:{PORT}")
    app.run(host="127.0.0.1", port=PORT, threaded=True, debug=False)
