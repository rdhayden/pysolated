"""Tests for codex command building (argv contents, flags, stdin).

The Codex provider builds an argv list (ADR 0001) plus stdin for the prompt;
the bypass flag is always emitted (symmetric with claude_code's default
``--dangerously-skip-permissions``); ``effort`` adds a ``-c`` override token
carrying literal TOML quotes because Codex's ``-c`` parses TOML and no shell
performs quote stripping.
"""

from __future__ import annotations

from pysolated import codex
from pysolated.core import AgentCommandOptions


def _build(agent, prompt: str = "say hi"):
    return agent.build_command(AgentCommandOptions(prompt=prompt))


def test_builds_argv_list_not_shell_string() -> None:
    command = _build(codex("gpt-5"))
    assert isinstance(command.argv, list)
    assert command.argv == [
        "codex",
        "exec",
        "--json",
        "--dangerously-bypass-approvals-and-sandbox",
        "-m",
        "gpt-5",
    ]


def test_prompt_delivered_on_stdin_not_argv() -> None:
    command = _build(codex("gpt-5"), prompt="do the thing")
    assert command.stdin == "do the thing"
    assert "do the thing" not in command.argv


def test_model_is_selectable() -> None:
    command = _build(codex("gpt-5-codex"))
    assert command.argv[command.argv.index("-m") + 1] == "gpt-5-codex"


def test_bypass_flag_is_always_present() -> None:
    """No opt-out, symmetric with claude_code's default skip-permissions."""
    no_effort = _build(codex("gpt-5"))
    with_effort = _build(codex("gpt-5", effort="high"))
    assert "--dangerously-bypass-approvals-and-sandbox" in no_effort.argv
    assert "--dangerously-bypass-approvals-and-sandbox" in with_effort.argv


def test_effort_adds_c_override_token_with_toml_quotes() -> None:
    """Codex's -c parses TOML; the value carries literal quotes verbatim."""
    command = _build(codex("gpt-5", effort="high"))
    assert "-c" in command.argv
    c_index = command.argv.index("-c")
    assert command.argv[c_index + 1] == 'model_reasoning_effort="high"'


def test_no_effort_omits_c_override() -> None:
    command = _build(codex("gpt-5"))
    assert "-c" not in command.argv


def test_provider_metadata() -> None:
    agent = codex("gpt-5", env={"FOO": "bar"})
    assert agent.name == "codex"
    assert agent.env == {"FOO": "bar"}
