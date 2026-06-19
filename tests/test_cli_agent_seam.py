"""CLI `--agent` / `--effort` seam (issue #32).

These verify the CLI flows through ``build_agent`` rather than constructing
``claude_code`` directly: the default still hands back a Claude Code provider,
unknown agents error with exit 2, and the foreign-flag rejections surface as
argument errors at the CLI boundary.
"""

from __future__ import annotations

from typing import Any

from typer.testing import CliRunner

from pysolated import RunResult
from pysolated import cli as cli_module
from pysolated.agents import ClaudeCode, Codex


def _fake_engine_capturing_kwargs(captured: dict) -> Any:
    async def fake_run(**kwargs: Any) -> RunResult:
        captured.update(kwargs)
        return RunResult(
            iterations=1,
            stdout="",
            branch="main",
        )

    return fake_run


def test_cli_default_agent_is_claude_code(monkeypatch: Any) -> None:
    """No --agent flag → claude-code, preserving today's behaviour byte-for-byte."""
    captured: dict = {}
    monkeypatch.setattr(
        cli_module, "run_engine", _fake_engine_capturing_kwargs(captured)
    )

    runner = CliRunner()
    result = runner.invoke(cli_module.app, ["run", "--prompt", "go"])
    assert result.exit_code == 0, result.output
    agent = captured["agent"]
    assert isinstance(agent, ClaudeCode)
    assert agent.model == "claude-opus-4-7"


def test_cli_explicit_agent_claude_code_runs_as_before(monkeypatch: Any) -> None:
    captured: dict = {}
    monkeypatch.setattr(
        cli_module, "run_engine", _fake_engine_capturing_kwargs(captured)
    )

    runner = CliRunner()
    result = runner.invoke(
        cli_module.app, ["run", "--agent", "claude-code", "--prompt", "go"]
    )
    assert result.exit_code == 0, result.output
    agent = captured["agent"]
    assert isinstance(agent, ClaudeCode)


def test_cli_unknown_agent_errors_exit_2_and_lists_valid_agents(
    monkeypatch: Any,
) -> None:
    captured: dict = {}
    monkeypatch.setattr(
        cli_module, "run_engine", _fake_engine_capturing_kwargs(captured)
    )

    runner = CliRunner()
    result = runner.invoke(
        cli_module.app, ["run", "--agent", "opencode", "--prompt", "go"]
    )
    assert result.exit_code == 2, result.output
    assert "opencode" in result.output
    assert "claude-code" in result.output
    # Engine was never invoked.
    assert captured == {}


def test_cli_effort_with_claude_code_errors_exit_2(monkeypatch: Any) -> None:
    captured: dict = {}
    monkeypatch.setattr(
        cli_module, "run_engine", _fake_engine_capturing_kwargs(captured)
    )

    runner = CliRunner()
    result = runner.invoke(
        cli_module.app,
        ["run", "--prompt", "go", "--effort", "high"],
    )
    assert result.exit_code == 2, result.output
    assert "--effort" in result.output
    assert "claude-code" in result.output
    assert captured == {}


def test_cli_codex_requires_model(monkeypatch: Any) -> None:
    """Missing --model with --agent codex errors at the argument boundary."""
    captured: dict = {}
    monkeypatch.setattr(
        cli_module, "run_engine", _fake_engine_capturing_kwargs(captured)
    )

    runner = CliRunner()
    result = runner.invoke(
        cli_module.app, ["run", "--agent", "codex", "--prompt", "go"]
    )
    assert result.exit_code == 2, result.output
    assert "--model" in result.output
    assert "codex" in result.output
    assert captured == {}


def test_cli_codex_with_model_and_effort(monkeypatch: Any) -> None:
    """`--agent codex --model gpt-5 --effort high` builds a configured Codex."""
    captured: dict = {}
    monkeypatch.setattr(
        cli_module, "run_engine", _fake_engine_capturing_kwargs(captured)
    )

    runner = CliRunner()
    result = runner.invoke(
        cli_module.app,
        [
            "run",
            "--agent",
            "codex",
            "--model",
            "gpt-5",
            "--effort",
            "high",
            "--prompt",
            "go",
        ],
    )
    assert result.exit_code == 0, result.output
    agent = captured["agent"]
    assert isinstance(agent, Codex)
    assert agent.model == "gpt-5"
    assert agent.effort == "high"


def test_cli_codex_rejects_permission_mode(monkeypatch: Any) -> None:
    """`--permission-mode` is meaningless to Codex and must be rejected."""
    captured: dict = {}
    monkeypatch.setattr(
        cli_module, "run_engine", _fake_engine_capturing_kwargs(captured)
    )

    runner = CliRunner()
    result = runner.invoke(
        cli_module.app,
        [
            "run",
            "--agent",
            "codex",
            "--model",
            "gpt-5",
            "--permission-mode",
            "auto",
            "--prompt",
            "go",
        ],
    )
    assert result.exit_code == 2, result.output
    assert "--permission-mode" in result.output
    assert "codex" in result.output
    assert captured == {}


def test_cli_invalid_effort_value_is_rejected(monkeypatch: Any) -> None:
    """`--effort` is constrained to low|medium|high|xhigh; an unknown value errors."""
    captured: dict = {}
    monkeypatch.setattr(
        cli_module, "run_engine", _fake_engine_capturing_kwargs(captured)
    )

    runner = CliRunner()
    result = runner.invoke(
        cli_module.app,
        ["run", "--prompt", "go", "--effort", "extreme"],
    )
    assert result.exit_code == 2, result.output
    assert captured == {}
