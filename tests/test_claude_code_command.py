"""Tests for claude_code command building (argv contents, flags, stdin)."""

from __future__ import annotations

from pysolated import claude_code
from pysolated.core import AgentCommandOptions


def _build(agent, prompt: str = "say hi"):
    return agent.build_command(AgentCommandOptions(prompt=prompt))


def test_builds_argv_list_not_shell_string() -> None:
    command = _build(claude_code("claude-opus-4-7"))
    assert isinstance(command.argv, list)
    assert command.argv == [
        "claude",
        "--print",
        "--verbose",
        "--dangerously-skip-permissions",
        "--output-format",
        "stream-json",
        "--model",
        "claude-opus-4-7",
        "-p",
        "-",
    ]


def test_prompt_delivered_on_stdin_not_argv() -> None:
    command = _build(claude_code("claude-opus-4-7"), prompt="do the thing")
    assert command.stdin == "do the thing"
    assert "do the thing" not in command.argv


def test_model_is_selectable() -> None:
    command = _build(claude_code("claude-haiku-4-5-20251001"))
    assert command.argv[command.argv.index("--model") + 1] == "claude-haiku-4-5-20251001"


def test_permission_mode_replaces_skip_permissions() -> None:
    command = _build(claude_code("claude-opus-4-7", permission_mode="auto"))
    assert "--dangerously-skip-permissions" not in command.argv
    assert command.argv[command.argv.index("--permission-mode") + 1] == "auto"


def test_default_uses_skip_permissions_and_no_permission_mode() -> None:
    command = _build(claude_code("claude-opus-4-7"))
    assert "--dangerously-skip-permissions" in command.argv
    assert "--permission-mode" not in command.argv


def test_provider_metadata() -> None:
    agent = claude_code("claude-opus-4-7", env={"FOO": "bar"})
    assert agent.name == "claude-code"
    assert agent.env == {"FOO": "bar"}
