"""CLI flag tests — verify --log-file and --name flow into run_engine().

We patch the engine import inside `pysolated.cli` to capture the kwargs the
CLI assembled. That keeps the CLI surface (Typer wiring + flag parsing) tested
without running a real agent.
"""

from __future__ import annotations

from pathlib import Path
from typing import Any

from typer.testing import CliRunner

from pysolated import RunResult
from pysolated import cli as cli_module


def _fake_engine_capturing_kwargs(captured: dict) -> Any:
    async def fake_run(**kwargs: Any) -> RunResult:
        captured.update(kwargs)
        return RunResult(
            iterations=1,
            stdout="",
            branch="main",
            log_file_path=str(kwargs.get("log_file") or "") or None,
        )

    return fake_run


def test_cli_log_file_flag_passes_path_to_engine(
    tmp_path: Path, monkeypatch: Any
) -> None:
    captured: dict = {}
    monkeypatch.setattr(
        cli_module, "run_engine", _fake_engine_capturing_kwargs(captured)
    )
    log_path = tmp_path / "run.log"

    runner = CliRunner()
    result = runner.invoke(
        cli_module.app,
        ["run", "--prompt", "say hi", "--log-file", str(log_path)],
    )
    assert result.exit_code == 0, result.output
    assert captured["log_file"] == str(log_path)


def test_cli_name_flag_passes_through_to_engine(monkeypatch: Any) -> None:
    captured: dict = {}
    monkeypatch.setattr(
        cli_module, "run_engine", _fake_engine_capturing_kwargs(captured)
    )

    runner = CliRunner()
    result = runner.invoke(
        cli_module.app,
        ["run", "--prompt", "say hi", "--name", "alpha"],
    )
    assert result.exit_code == 0, result.output
    assert captured["name"] == "alpha"


def test_cli_without_log_file_omits_flag(monkeypatch: Any) -> None:
    captured: dict = {}
    monkeypatch.setattr(
        cli_module, "run_engine", _fake_engine_capturing_kwargs(captured)
    )

    runner = CliRunner()
    result = runner.invoke(cli_module.app, ["run", "--prompt", "say hi"])
    assert result.exit_code == 0, result.output
    assert captured.get("log_file") is None
