"""Tests for the no_sandbox provider against real host subprocesses.

The factory (`no_sandbox()`) is the user-facing seam; the live handle returned
by `provider.create(work_dir)` is what owns `exec()`. These tests drive the
handle so a regression in either layer surfaces here.
"""

from __future__ import annotations

import asyncio

import pytest

from pysolated import no_sandbox
from pysolated.sandboxes import NoSandboxHandle


async def _handle(*, env: dict[str, str] | None = None) -> NoSandboxHandle:
    return await no_sandbox(env=env or {}).create(work_dir=".")


async def test_streams_stdout_lines_via_on_line() -> None:
    lines: list[str] = []
    handle = await _handle()
    result = await handle.exec(["printf", "one\ntwo\nthree\n"], on_line=lines.append)
    assert lines == ["one", "two", "three"]
    assert result.exit_code == 0
    assert result.stdout == "one\ntwo\nthree"


async def test_returns_exit_code_and_stderr() -> None:
    handle = await _handle()
    result = await handle.exec(
        ["sh", "-c", "echo oops >&2; exit 3"], on_line=lambda _l: None
    )
    assert result.exit_code == 3
    assert "oops" in result.stderr


async def test_feeds_stdin_to_subprocess() -> None:
    handle = await _handle()
    result = await handle.exec(["cat"], stdin="piped input")
    assert result.stdout == "piped input"
    assert result.exit_code == 0


async def test_runs_in_given_cwd(tmp_path) -> None:
    handle = await _handle()
    result = await handle.exec(["pwd"], cwd=str(tmp_path))
    # macOS resolves /tmp symlinks; compare the basename to stay portable.
    assert result.stdout.strip().endswith(tmp_path.name)


async def test_injects_env() -> None:
    handle = await _handle(env={"PYSOLATED_TEST": "xyz"})
    result = await handle.exec(["sh", "-c", "echo $PYSOLATED_TEST"])
    assert result.stdout.strip() == "xyz"


async def test_cancellation_kills_subprocess() -> None:
    handle = await _handle()
    task = asyncio.create_task(handle.exec(["sleep", "30"]))
    await asyncio.sleep(0.1)
    task.cancel()
    with pytest.raises(asyncio.CancelledError):
        # Should return promptly because the subprocess is killed.
        await asyncio.wait_for(task, timeout=5)


async def test_close_is_idempotent_noop() -> None:
    """`close()` is a no-op on no-sandbox — calling twice must not raise."""
    handle = await _handle()
    await handle.close()
    await handle.close()


async def test_provider_is_a_factory_returning_handle() -> None:
    """`no_sandbox()` returns a frozen factory; `create()` yields a handle.

    The handle exposes `exec` + `close`; the provider itself does not — that
    split is the whole point of ADR 0003.
    """
    provider = no_sandbox()
    assert not hasattr(provider, "exec")
    handle = await provider.create(work_dir=".")
    assert hasattr(handle, "exec")
    assert hasattr(handle, "close")


async def test_provider_create_returns_independent_handles() -> None:
    """Each `create()` call yields a fresh handle — concurrency-safety guarantee."""
    provider = no_sandbox(env={"X": "1"})
    h1 = await provider.create(work_dir=".")
    h2 = await provider.create(work_dir=".")
    assert h1 is not h2
    # Mutating one handle's env must not bleed into the other.
    h1.env["X"] = "tampered"
    assert h2.env["X"] == "1"
