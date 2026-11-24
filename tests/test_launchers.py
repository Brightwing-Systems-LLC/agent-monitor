import base64

import pytest

from claude_monitor import actions

SESSION = {
    "agent": "claude",
    "name": "demo",
    "cwd": "/w",
    "host": "",
    "command": {
        "resumable": True,
        "resume": "claude --resume abc",
        "shell": "cd /w && claude --resume abc",
    },
}


@pytest.fixture
def everything_installed(monkeypatch):
    monkeypatch.setattr(actions.os.path, "exists", lambda p: True)
    monkeypatch.setattr(actions, "_tmux_running", lambda: True)


def test_macos_order_and_own_host_first(everything_installed):
    names = [f.__name__ for f in actions.launchers_for("", "darwin")]
    assert names == ["launch_cmux", "launch_herdr", "launch_terminal_app"]
    names = [f.__name__ for f in actions.launchers_for("herdr", "darwin")]
    assert names[0] == "launch_herdr"


def test_linux_order(everything_installed):
    names = [f.__name__ for f in actions.launchers_for("tmux", "linux")]
    assert names == ["launch_tmux", "launch_herdr", "launch_linux_terminal"]


def test_windows_order():
    names = [f.__name__ for f in actions.launchers_for("Windows Terminal", "win32")]
    assert names == ["launch_windows_terminal", "launch_windows_console"]


def test_windows_terminal_passes_an_encoded_command(monkeypatch):
    seen = {}
    monkeypatch.setattr(
        actions.shutil, "which", lambda n: {"wt": "wt.exe", "pwsh": "pwsh.exe"}.get(n)
    )
    monkeypatch.setattr(actions, "_spawn", lambda argv, who, **kw: seen.update(argv=argv))
    cmd = "$env:CLAUDE_CONFIG_DIR='C:\\x'; claude --resume 'a b'"
    assert (
        actions.launch_windows_terminal(
            {**SESSION, "cwd": r"C:\w", "command": {**SESSION["command"], "resume": cmd}}
        )
        == "Windows Terminal"
    )
    argv = seen["argv"]
    assert argv[:6] == ["wt.exe", "-w", "0", "new-tab", "-d", r"C:\w"]
    assert ";" not in " ".join(argv)
    assert base64.b64decode(argv[-1]).decode("utf-16-le") == cmd


def test_terminal_app_escapes_applescript(monkeypatch):
    seen = {}

    class Done:
        returncode, stdout, stderr = 0, "", ""

    monkeypatch.setattr(
        actions.subprocess, "run", lambda argv, **kw: seen.update(argv=argv) or Done
    )
    s = {**SESSION, "command": {**SESSION["command"], "shell": 'cd "/a" && echo \\ok'}}
    actions.launch_terminal_app(s)
    assert 'do script "cd \\"/a\\" && echo \\\\ok"' in seen["argv"][2]


def test_launch_reports_every_failure(monkeypatch):
    def boom(name):
        def f(session):
            raise actions.ActionError(f"{name} said no")

        return f

    monkeypatch.setattr(actions, "launchers_for", lambda host: [boom("one"), boom("two")])
    with pytest.raises(actions.ActionError, match="one said no; two said no"):
        actions.launch(SESSION)


def test_unresumable_session_is_refused():
    with pytest.raises(actions.ActionError):
        actions.launch({**SESSION, "command": {"resumable": False}})
