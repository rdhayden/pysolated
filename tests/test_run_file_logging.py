"""run()-level tests for file logging: log_file= and RunResult.log_file_path.

Uses the same fake seams as test_orchestrator so the file display is exercised
end-to-end without a real subprocess.
"""

from __future__ import annotations

from pathlib import Path

import pytest

from pysolated import FileDisplay, run

from .test_orchestrator import FakeAgent, FakeSandbox, _assistant, USAGE


async def test_log_file_path_set_on_run_result(tmp_path: Path) -> None:
    log_path = tmp_path / "run.log"
    lines = [_assistant([{"type": "text", "text": "ok"}], usage=USAGE)]
    result = await run(
        agent=FakeAgent(lines),
        sandbox=FakeSandbox(lines),
        prompt="go",
        log_file=log_path,
    )
    assert result.log_file_path == str(log_path)


async def test_log_file_path_none_by_default(tmp_path: Path) -> None:
    lines = [_assistant([{"type": "text", "text": "ok"}])]
    result = await run(
        agent=FakeAgent(lines),
        sandbox=FakeSandbox(lines),
        prompt="go",
    )
    assert result.log_file_path is None


async def test_log_file_routes_progress_and_agent_output_to_file(
    tmp_path: Path,
) -> None:
    log_path = tmp_path / "run.log"
    lines = [
        _assistant(
            [
                {"type": "text", "text": "agent says hello"},
                {"type": "tool_use", "name": "Bash", "input": {"command": "ls"}},
            ],
            usage=USAGE,
        )
    ]
    await run(
        agent=FakeAgent(lines),
        sandbox=FakeSandbox(lines),
        prompt="go",
        log_file=log_path,
    )
    contents = log_path.read_text()
    assert "agent says hello" in contents  # agent prose written
    assert "Bash" in contents  # tool call written
    assert "Iteration 1/1" in contents  # status line written
    assert "Run summary" in contents  # summary written


async def test_log_file_carries_run_name_in_header(tmp_path: Path) -> None:
    log_path = tmp_path / "run.log"
    lines = [_assistant([{"type": "text", "text": "ok"}])]
    await run(
        agent=FakeAgent(lines),
        sandbox=FakeSandbox(lines),
        prompt="go",
        name="nightly",
        log_file=log_path,
    )
    head = log_path.read_text().splitlines()[0]
    assert "nightly" in head


async def test_log_file_status_lines_carry_run_name(tmp_path: Path) -> None:
    log_path = tmp_path / "run.log"
    lines = [_assistant([{"type": "text", "text": "ok"}])]
    await run(
        agent=FakeAgent(lines),
        sandbox=FakeSandbox(lines),
        prompt="go",
        name="nightly",
        log_file=log_path,
    )
    iteration_line = next(
        line for line in log_path.read_text().splitlines() if "Iteration 1/1" in line
    )
    assert "[nightly]" in iteration_line


async def test_log_file_and_explicit_display_rejected_as_mutex(
    tmp_path: Path,
) -> None:
    """Passing both log_file= and display= is rejected up front."""
    log_path = tmp_path / "run.log"
    lines = [_assistant([{"type": "text", "text": "ok"}])]
    with pytest.raises(ValueError):
        await run(
            agent=FakeAgent(lines),
            sandbox=FakeSandbox(lines),
            prompt="go",
            log_file=log_path,
            display=FileDisplay(log_path),
        )
