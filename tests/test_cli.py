"""CLI flag tests — verify --log-file and --name flow into run_engine().

We patch the engine import inside `pysolated.cli` to capture the kwargs the
CLI assembled. That keeps the CLI surface (Typer wiring + flag parsing) tested
without running a real agent.
"""

from __future__ import annotations

import asyncio
import os
import signal as os_signal
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


def test_cli_passes_abort_signal_event_to_engine(monkeypatch: Any) -> None:
    """The CLI must hand `signal=asyncio.Event` to `run_engine` for SIGINT wiring."""
    captured: dict = {}
    monkeypatch.setattr(
        cli_module, "run_engine", _fake_engine_capturing_kwargs(captured)
    )

    runner = CliRunner()
    result = runner.invoke(cli_module.app, ["run", "--prompt", "go"])
    assert result.exit_code == 0, result.output
    assert isinstance(captured["signal"], asyncio.Event)


def test_cli_sigint_sets_abort_signal_and_exits_130(monkeypatch: Any) -> None:
    """SIGINT during a run must set the abort event and surface exit 130.

    The fake engine schedules a SIGINT to the current process while it is
    awaiting, then verifies the CLI's installed handler set the event before
    raising `CancelledError` (mimicking the orchestrator's abort path).
    """
    captured: dict = {}

    async def fake_engine(**kwargs: Any) -> RunResult:
        captured.update(kwargs)
        abort: asyncio.Event = kwargs["signal"]
        loop = asyncio.get_running_loop()
        loop.call_soon(lambda: os.kill(os.getpid(), os_signal.SIGINT))
        try:
            await asyncio.wait_for(abort.wait(), timeout=2.0)
        except asyncio.TimeoutError:  # pragma: no cover - assertion path
            raise AssertionError("CLI did not set the abort event on SIGINT")
        raise asyncio.CancelledError("aborted by signal")

    monkeypatch.setattr(cli_module, "run_engine", fake_engine)

    runner = CliRunner()
    result = runner.invoke(cli_module.app, ["run", "--prompt", "go"])
    assert result.exit_code == 130, result.output
    assert isinstance(captured["signal"], asyncio.Event)


def test_cli_agent_execution_error_exits_1_with_diagnostic_on_stderr(
    monkeypatch: Any,
) -> None:
    """A crashed agent yields exit 1 and prints the stderr/output tail.

    Without the tail on stderr the user just sees "Iterations: 1 / 1" or a
    bare traceback — neither tells them why the agent died.
    """
    from pysolated.errors import AgentExecutionError

    async def crashing_engine(**kwargs: Any) -> RunResult:
        raise AgentExecutionError(
            exit_code=42,
            stderr="ENOENT: cannot find 'claude' binary",
            stdout_tail="",
        )

    monkeypatch.setattr(cli_module, "run_engine", crashing_engine)

    runner = CliRunner()
    result = runner.invoke(cli_module.app, ["run", "--prompt", "go"])
    assert result.exit_code == 1, result.output
    # The tail must reach the user (typer.echo with err=True; CliRunner mixes
    # stdout+stderr into `output` by default).
    assert "ENOENT" in result.output
    assert "claude" in result.output
    assert "42" in result.output


def test_cli_without_log_file_omits_flag(monkeypatch: Any) -> None:
    captured: dict = {}
    monkeypatch.setattr(
        cli_module, "run_engine", _fake_engine_capturing_kwargs(captured)
    )

    runner = CliRunner()
    result = runner.invoke(cli_module.app, ["run", "--prompt", "say hi"])
    assert result.exit_code == 0, result.output
    assert captured.get("log_file") is None


# ---------------------------------------------------------------------------
# `pysolated podman build-image` / `remove-image` (issue #22).
# ---------------------------------------------------------------------------


def _fake_subprocess_recorder() -> tuple[list[list[str]], Any]:
    """Record argv from `_stream_subprocess` for podman-CLI shape assertions."""
    from pysolated import ExecResult

    calls: list[list[str]] = []

    async def fake(argv: list[str], **kwargs: Any) -> ExecResult:
        calls.append(list(argv))
        return ExecResult(exit_code=0, stdout="", stderr="")

    return calls, fake


def test_cli_podman_build_image_default_containerfile(
    tmp_path: Path, monkeypatch: Any
) -> None:
    """`pysolated podman build-image` runs `podman build -f Containerfile -t <derived>`."""
    project = tmp_path / "demo"
    project.mkdir()
    monkeypatch.chdir(project)
    calls, fake = _fake_subprocess_recorder()
    monkeypatch.setattr("pysolated.sandboxes._stream_subprocess", fake)

    runner = CliRunner()
    result = runner.invoke(cli_module.app, ["podman", "build-image"])
    assert result.exit_code == 0, result.output
    assert calls == [
        [
            "podman",
            "build",
            "-f",
            "Containerfile",
            "-t",
            "pysolated:demo",
            str(project),
        ]
    ]


def test_cli_podman_build_image_custom_file_flag(
    tmp_path: Path, monkeypatch: Any
) -> None:
    """`--file` overrides the Containerfile path."""
    project = tmp_path / "demo"
    project.mkdir()
    monkeypatch.chdir(project)
    calls, fake = _fake_subprocess_recorder()
    monkeypatch.setattr("pysolated.sandboxes._stream_subprocess", fake)

    runner = CliRunner()
    result = runner.invoke(
        cli_module.app,
        ["podman", "build-image", "--file", "docker/Dev.Containerfile"],
    )
    assert result.exit_code == 0, result.output
    argv = calls[0]
    i = argv.index("-f")
    assert argv[i + 1] == "docker/Dev.Containerfile"


def test_cli_podman_build_image_explicit_tag(tmp_path: Path, monkeypatch: Any) -> None:
    """An explicit `--image` skips derivation."""
    monkeypatch.chdir(tmp_path)
    calls, fake = _fake_subprocess_recorder()
    monkeypatch.setattr("pysolated.sandboxes._stream_subprocess", fake)

    runner = CliRunner()
    result = runner.invoke(
        cli_module.app,
        ["podman", "build-image", "--image", "custom:tag"],
    )
    assert result.exit_code == 0, result.output
    argv = calls[0]
    i = argv.index("-t")
    assert argv[i + 1] == "custom:tag"


def test_cli_podman_remove_image_default_tag(tmp_path: Path, monkeypatch: Any) -> None:
    """`pysolated podman remove-image` runs `podman rmi <derived>` by default."""
    project = tmp_path / "demo"
    project.mkdir()
    monkeypatch.chdir(project)
    calls, fake = _fake_subprocess_recorder()
    monkeypatch.setattr("pysolated.sandboxes._stream_subprocess", fake)

    runner = CliRunner()
    result = runner.invoke(cli_module.app, ["podman", "remove-image"])
    assert result.exit_code == 0, result.output
    assert calls == [["podman", "rmi", "pysolated:demo"]]


def test_cli_podman_remove_image_explicit_tag(tmp_path: Path, monkeypatch: Any) -> None:
    monkeypatch.chdir(tmp_path)
    calls, fake = _fake_subprocess_recorder()
    monkeypatch.setattr("pysolated.sandboxes._stream_subprocess", fake)

    runner = CliRunner()
    result = runner.invoke(
        cli_module.app,
        ["podman", "remove-image", "--image", "custom:tag"],
    )
    assert result.exit_code == 0, result.output
    assert calls == [["podman", "rmi", "custom:tag"]]


def test_cli_podman_build_image_nonzero_exit_propagates(
    tmp_path: Path, monkeypatch: Any
) -> None:
    """A failing `podman build` surfaces a non-zero exit and the stderr."""
    from pysolated import ExecResult

    monkeypatch.chdir(tmp_path)

    async def failing(argv: list[str], **kwargs: Any) -> ExecResult:
        return ExecResult(exit_code=2, stdout="", stderr="syntax error")

    monkeypatch.setattr("pysolated.sandboxes._stream_subprocess", failing)
    runner = CliRunner()
    result = runner.invoke(cli_module.app, ["podman", "build-image"])
    assert result.exit_code == 2, result.output
    assert "syntax error" in result.output
