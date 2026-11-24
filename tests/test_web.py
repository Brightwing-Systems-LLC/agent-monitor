import pytest

from claude_monitor import actions, web


@pytest.fixture
def client(tmp_path, monkeypatch):
    monkeypatch.setattr(web, "stopped", actions.StoppedStore(tmp_path / "stopped.json"))
    web._last.clear()
    web._last[4242] = {
        "pid": 4242,
        "lstart": "Tue Nov 24 13:23:25 2026",
        "name": "x",
        "command": {"resumable": True},
    }
    return web.app.test_client()


def test_rejects_foreign_host_header(client):
    assert client.get("/", headers={"Host": "evil.example:8447"}).status_code == 403


def test_post_requires_custom_header(client):
    r = client.post(
        "/api/session/4242/stop",
        json={"lstart": "Tue Nov 24 13:23:25 2026"},
        headers={"Host": "localhost:8447"},
    )
    assert r.status_code == 403


def test_post_with_stale_lstart_is_refused_before_any_signal(client, monkeypatch):
    monkeypatch.setattr(actions, "stop", lambda *a, **k: pytest.fail("signalled"))
    r = client.post(
        "/api/session/4242/stop",
        json={"lstart": "Mon Nov 23 00:00:00 2026"},
        headers={"Host": "localhost:8447", "X-Claude-Monitor": "1"},
    )
    assert r.status_code == 409


def test_verify_identity_refuses_a_recycled_pid(monkeypatch):
    from claude_monitor.procinfo import Proc

    monkeypatch.setattr(
        actions.procinfo,
        "process_table",
        lambda: {7: Proc(7, 1, 0, 0, "Tue Nov 24 13:23:25 2026", "/bin/zsh")},
    )
    with pytest.raises(actions.ActionError):
        actions.verify_identity(7, "Tue Nov 24 13:23:25 2026")


def test_stopped_store_round_trip(tmp_path):
    store = actions.StoppedStore(tmp_path / "s.json")
    store.add({"session_id": "s1", "name": "n", "command": {"shell": "x"}})
    assert store.get("s1")["name"] == "n"
    store.remove("s1")
    assert store.load() == []


def test_stopped_entry_survives_a_poll_while_the_process_is_still_exiting(client, monkeypatch):
    web.stopped.add({"session_id": "sid-x", "name": "x", "command": {"shell": "s"}})
    stopped_at = web.stopped.get("sid-x")["stopped_at"]
    still_exiting = {"pid": 1, "lstart": "", "session_id": "sid-x", "started_at": stopped_at - 60}
    monkeypatch.setattr(
        web.monitor, "gather", lambda: {"generated_at": 0, "sessions": [still_exiting], "stale": []}
    )
    client.get("/api/sessions", headers={"Host": "localhost:8447"})
    assert web.stopped.get("sid-x") is not None

    relaunched = still_exiting | {"started_at": stopped_at + 5}
    monkeypatch.setattr(
        web.monitor, "gather", lambda: {"generated_at": 0, "sessions": [relaunched], "stale": []}
    )
    client.get("/api/sessions", headers={"Host": "localhost:8447"})
    assert web.stopped.get("sid-x") is None


def test_favicons_are_served(client):
    for path in ("/favicon.ico", "/apple-touch-icon.png", "/static/favicon.svg"):
        r = client.get(path, headers={"Host": "localhost:8447"})
        assert r.status_code == 200 and r.data, path
