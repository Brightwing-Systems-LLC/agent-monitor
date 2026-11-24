import re
import shlex

from claude_monitor import commands
from claude_monitor.agents import claude
from claude_monitor.commands import POSIX, PWSH


def test_claude_strips_resume_flags_and_their_values():
    args = ["--settings", "s.json", "--resume", "abc", "--dangerously-skip-permissions"]
    assert claude.strip_resume_args(args) == (
        ["--settings", "s.json", "--dangerously-skip-permissions"],
        None,
    )


def test_claude_strips_bare_resume_continue_and_session_id():
    args = ["--continue", "--session-id", "x", "--resume", "--model", "opus", "-r=abc"]
    assert claude.strip_resume_args(args) == (["--model", "opus"], None)


def test_claude_drops_trailing_prompt_after_boolean_flag():
    args = ["--dangerously-skip-permissions", "--resume", "id1", "Picking up prepared work"]
    out, dropped = claude.strip_resume_args(args)
    assert out == ["--dangerously-skip-permissions"]
    assert dropped == "Picking up prepared work"


def test_keeps_trailing_value_of_a_value_flag():
    out, dropped = claude.strip_resume_args(["--name", "my session"])
    assert out == ["--name", "my session"] and dropped is None


def test_split_env_keeps_config_dir_and_skips_runtime_and_secrets():
    env = {
        "CLAUDE_CONFIG_DIR": "/x",
        "CLAUDE_CODE_SESSION_ID": "s",
        "CLAUDECODE": "1",
        "ANTHROPIC_API_KEY": "sk",
        "ANTHROPIC_MODEL": "m",
        "PATH": "/bin",
        "CMUX_SURFACE": "a",
    }
    keep, secret = commands.split_env(env, claude._PASSTHROUGH_ENV, claude._RUNTIME_ENV)
    assert keep == {"ANTHROPIC_MODEL": "m", "CLAUDE_CONFIG_DIR": "/x"}
    assert secret == ["ANTHROPIC_API_KEY"]


def test_claude_command_round_trips_json_argument_through_a_posix_shell():
    mcp = '--mcp-config={"mcpServers": {"a": {"command": "/x y"}}}'
    argv = [
        "/Users/u/.local/share/claude/versions/2.1.0",
        "--settings",
        "/s.json",
        mcp,
        "--dangerously-skip-permissions",
    ]
    cmd = claude.build_command(argv, {"CLAUDE_CONFIG_DIR": "/c"}, "/work dir", "sid-1", POSIX)
    assert cmd["resumable"]
    assert cmd["shell"].startswith("cd '/work dir' && CLAUDE_CONFIG_DIR=/c claude ")
    assert shlex.split(cmd["resume"]) == [
        "CLAUDE_CONFIG_DIR=/c",
        "claude",
        "--settings",
        "/s.json",
        mcp,
        "--dangerously-skip-permissions",
        "--resume",
        "sid-1",
    ]


def test_claude_print_mode_and_missing_session_are_not_resumable():
    assert not claude.build_command(["claude", "-p", "hi"], {}, "/w", "sid", POSIX)["resumable"]
    assert not claude.build_command(["claude"], {}, "/w", None, POSIX)["resumable"]


def _pwsh_unquote(words: str) -> list[str]:
    """Minimal PowerShell tokenizer for what render() emits: bare words and '...' literals."""
    out = []
    for m in re.finditer(r"'((?:[^']|'')*)'|(\S+)", words):
        out.append(m.group(1).replace("''", "'") if m.group(1) is not None else m.group(2))
    return out


def test_powershell_rendering_quotes_literally_and_sets_env():
    argv = [
        r"C:\Program Files\claude\claude.exe",
        "--name",
        "it's mine",
        '--mcp-config={"a": 1}',
        "--resume",
        "abc-123",
    ]
    text = commands.render(argv, {"CLAUDE_CONFIG_DIR": r"C:\Users\me\.claude-x"}, PWSH)
    assert text.startswith(r"$env:CLAUDE_CONFIG_DIR='C:\Users\me\.claude-x'; & '")
    call = text.split("; ", 1)[1].removeprefix("& ")
    assert _pwsh_unquote(call) == argv


def test_powershell_cd_and_bare_program():
    text = commands.with_cd(
        r"C:\work dir", commands.render(["codex", "resume", "x"], {}, PWSH), PWSH
    )
    assert text == r"Set-Location -LiteralPath 'C:\work dir'; codex resume x"


def test_short_program_collapses_pinned_binaries():
    assert (
        commands.short_program(
            "/opt/x/.local/share/claude/versions/2.1.9", "claude", claude._PINNED
        )
        == "claude"
    )
    assert (
        commands.short_program("/somewhere/else/claude-dev", "claude")
        == "/somewhere/else/claude-dev"
    )
