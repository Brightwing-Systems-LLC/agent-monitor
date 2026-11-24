import shlex

from claude_monitor import commands


def test_strips_resume_flags_and_their_values():
    args = ["--settings", "s.json", "--resume", "abc", "--dangerously-skip-permissions"]
    assert commands.strip_resume_args(args) == (
        ["--settings", "s.json", "--dangerously-skip-permissions"],
        None,
    )


def test_strips_bare_resume_continue_and_session_id():
    args = ["--continue", "--session-id", "x", "--resume", "--model", "opus", "-r=abc"]
    assert commands.strip_resume_args(args) == (["--model", "opus"], None)


def test_drops_trailing_positional_prompt_after_boolean_flag():
    args = ["--dangerously-skip-permissions", "--resume", "id1", "Picking up prepared work"]
    out, dropped = commands.strip_resume_args(args)
    assert out == ["--dangerously-skip-permissions"]
    assert dropped == "Picking up prepared work"


def test_keeps_trailing_value_of_a_value_flag():
    out, dropped = commands.strip_resume_args(["--name", "my session"])
    assert out == ["--name", "my session"] and dropped is None


def test_launch_env_keeps_config_dir_and_skips_runtime_and_secrets():
    env = {
        "CLAUDE_CONFIG_DIR": "/x",
        "CLAUDE_CODE_SESSION_ID": "s",
        "CLAUDECODE": "1",
        "ANTHROPIC_API_KEY": "sk",
        "ANTHROPIC_MODEL": "m",
        "PATH": "/bin",
        "CMUX_SURFACE": "a",
    }
    keep, secret = commands.launch_env(env)
    assert keep == {"ANTHROPIC_MODEL": "m", "CLAUDE_CONFIG_DIR": "/x"}
    assert secret == ["ANTHROPIC_API_KEY"]


def test_build_command_round_trips_json_argument_through_the_shell():
    mcp = '--mcp-config={"mcpServers": {"a": {"command": "/x y"}}}'
    argv = [
        "/Users/u/.local/share/claude/versions/2.1.0",
        "--settings",
        "/s.json",
        mcp,
        "--dangerously-skip-permissions",
    ]
    cmd = commands.build_command(argv, {"CLAUDE_CONFIG_DIR": "/c"}, "/work dir", "sid-1")
    assert cmd["resumable"]
    assert cmd["shell"].startswith("cd '/work dir' && CLAUDE_CONFIG_DIR=/c claude ")
    words = shlex.split(cmd["resume"])
    assert words == [
        "CLAUDE_CONFIG_DIR=/c",
        "claude",
        "--settings",
        "/s.json",
        mcp,
        "--dangerously-skip-permissions",
        "--resume",
        "sid-1",
    ]


def test_print_mode_is_not_resumable():
    cmd = commands.build_command(["claude", "-p", "hi"], {}, "/w", "sid")
    assert not cmd["resumable"]


def test_no_session_id_is_not_resumable():
    assert not commands.build_command(["claude"], {}, "/w", None)["resumable"]
