"""Tests for the no_sandbox provider against real host subprocesses."""

from __future__ import annotations

import asyncio

import pytest

from pysolated import no_sandbox


async def test_streams_stdout_lines_via_on_line() -> None:
    lines: list[str] = []
    result = await no_sandbox().exec(
        ["printf", "one\ntwo\nthree\n"], on_line=lines.append
    )
    assert lines == ["one", "two", "three"]
    assert result.exit_code == 0
    assert result.stdout == "one\ntwo\nthree"


async def test_returns_exit_code_and_stderr() -> None:
    result = await no_sandbox().exec(
        ["sh", "-c", "echo oops >&2; exit 3"], on_line=lambda _l: None
    )
    assert result.exit_code == 3
    assert "oops" in result.stderr


async def test_feeds_stdin_to_subprocess() -> None:
    result = await no_sandbox().exec(["cat"], stdin="piped input")
    assert result.stdout == "piped input"
    assert result.exit_code == 0


async def test_runs_in_given_cwd(tmp_path) -> None:
    result = await no_sandbox().exec(["pwd"], cwd=str(tmp_path))
    # macOS resolves /tmp symlinks; compare the basename to stay portable.
    assert result.stdout.strip().endswith(tmp_path.name)


async def test_injects_env() -> None:
    result = await no_sandbox(env={"PYSOLATED_TEST": "xyz"}).exec(
        ["sh", "-c", "echo $PYSOLATED_TEST"]
    )
    assert result.stdout.strip() == "xyz"


async def test_cancellation_kills_subprocess() -> None:
    task = asyncio.create_task(no_sandbox().exec(["sleep", "30"]))
    await asyncio.sleep(0.1)
    task.cancel()
    with pytest.raises(asyncio.CancelledError):
        # Should return promptly because the subprocess is killed.
        await asyncio.wait_for(task, timeout=5)
