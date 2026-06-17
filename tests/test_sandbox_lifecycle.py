"""Tests for the sandbox factory/handle lifecycle wired into `run()`.

The orchestrator must call `provider.create(work_dir)` once before the first
exec and `handle.close()` in a `finally` that covers every exit path: normal
success, an exception raised during the run, and an `asyncio.CancelledError`
from an abort signal. A per-handle `atexit` backstop catches the corner case
where the orchestrator's `finally` never ran (e.g. abnormal interpreter
shutdown) — this is exercised by inspecting the registered callback.

Drives the orchestrator with fake seams so no real subprocess or file I/O is
needed; the assertions watch the lifecycle directly.
"""

from __future__ import annotations

import asyncio
import atexit
import json
from typing import Callable
from unittest.mock import patch

import pytest

from pysolated import (
    AgentCommandOptions,
    Command,
    ExecResult,
    Severity,
    parse_session_usage,
    parse_stream_line,
    run,
)


class TrivialAgent:
    """Minimal agent — emits one assistant line; otherwise a passthrough."""

    name = "trivial"
    env: dict[str, str] = {}

    def build_command(self, options: AgentCommandOptions) -> Command:
        return Command(argv=["fake"], stdin=options.prompt)

    def parse_stream_line(self, line: str):
        return parse_stream_line(line)

    def parse_session_usage(self, content: str):
        return parse_session_usage(content)


def _assistant(text: str) -> str:
    return json.dumps(
        {"type": "assistant", "message": {"content": [{"type": "text", "text": text}]}}
    )


class LifecycleProvider:
    """Factory whose `create()` returns a fresh handle each call.

    Records every create/close so tests can assert how many handles the
    orchestrator built and whether each one was torn down.
    """

    name = "lifecycle"
    env: dict[str, str] = {}

    def __init__(self, *, agent_lines: list[str] | None = None) -> None:
        self._agent_lines = (
            agent_lines if agent_lines is not None else [_assistant("ok")]
        )
        self.created: list[LifecycleHandle] = []

    async def create(self, work_dir: str) -> "LifecycleHandle":
        handle = LifecycleHandle(self._agent_lines)
        self.created.append(handle)
        return handle


class LifecycleHandle:
    """Live handle: answers git probes, streams the scripted agent lines.

    `close_count` counts every `close()` call so an idempotency violation
    (e.g. atexit running on top of `finally`) is observable.
    """

    def __init__(self, agent_lines: list[str]) -> None:
        self._agent_lines = agent_lines
        self.close_count = 0
        self.exec_count = 0

    async def exec(
        self,
        argv: list[str],
        *,
        stdin: str | None = None,
        cwd: str | None = None,
        on_line: Callable[[str], None] | None = None,
    ) -> ExecResult:
        self.exec_count += 1
        if argv[:2] == ["git", "rev-parse"] and "--abbrev-ref" in argv:
            return ExecResult(exit_code=0, stdout="main\n", stderr="")
        if argv[:2] == ["git", "rev-parse"]:
            return ExecResult(exit_code=0, stdout="deadbeef\n", stderr="")
        if argv[:2] == ["git", "rev-list"]:
            return ExecResult(exit_code=0, stdout="", stderr="")
        for line in self._agent_lines:
            if on_line is not None:
                on_line(line)
        return ExecResult(exit_code=0, stdout="\n".join(self._agent_lines), stderr="")

    async def close(self) -> None:
        self.close_count += 1


class _NullDisplay:
    def intro(self, title: str) -> None: ...
    def status(self, message: str, severity: Severity) -> None: ...
    def text(self, message: str) -> None: ...
    def tool_call(self, name: str, formatted_args: str) -> None: ...
    def summary(self, title: str, rows: dict[str, str]) -> None: ...


async def test_create_called_once_before_first_exec() -> None:
    provider = LifecycleProvider()
    await run(
        agent=TrivialAgent(),
        sandbox=provider,
        prompt="go",
        display=_NullDisplay(),
    )
    assert len(provider.created) == 1
    # Exec was actually used (git probes + agent invocation).
    assert provider.created[0].exec_count >= 1


async def test_close_called_on_normal_success() -> None:
    provider = LifecycleProvider()
    await run(
        agent=TrivialAgent(),
        sandbox=provider,
        prompt="go",
        display=_NullDisplay(),
    )
    handle = provider.created[0]
    assert handle.close_count == 1, (
        f"expected one close on success; got {handle.close_count}"
    )


async def test_close_called_when_run_raises_exception() -> None:
    """A user-visible error (here: structured-output guard) must still close()."""
    from pysolated import Output
    from pydantic import BaseModel

    class Out(BaseModel):
        x: int

    provider = LifecycleProvider()
    # The opening tag is absent from the prompt → run() raises ValueError
    # *after* create() but before the agent runs.
    with pytest.raises(ValueError):
        await run(
            agent=TrivialAgent(),
            sandbox=provider,
            prompt="no tag here",
            display=_NullDisplay(),
            output=Output.object("result", Out),
        )
    handle = provider.created[0]
    assert handle.close_count == 1, (
        "close() must run even when the orchestrator raises after create()"
    )


async def test_close_called_when_signal_aborts_run() -> None:
    """Aborting via `signal=` must still tear the handle down."""
    provider = LifecycleProvider()
    abort = asyncio.Event()
    abort.set()  # pre-fired so the loop bails before iteration 1

    with pytest.raises(asyncio.CancelledError):
        await run(
            agent=TrivialAgent(),
            sandbox=provider,
            prompt="go",
            display=_NullDisplay(),
            signal=abort,
        )
    handle = provider.created[0]
    assert handle.close_count == 1


async def test_atexit_backstop_registered_during_run_and_unregistered_after() -> None:
    """The `atexit` handler exists only while the handle is live.

    Whilst `run()` is executing, an `atexit` callback for the live handle is
    registered (so an abnormal interpreter exit still tears it down). Once
    `run()` returns, the orchestrator's `finally` unregisters that callback
    so a successful run doesn't accumulate dead handlers.
    """
    registered: list[Callable[..., object]] = []
    unregistered: list[Callable[..., object]] = []

    real_register = atexit.register
    real_unregister = atexit.unregister

    def tracking_register(func, *args, **kwargs):
        registered.append(func)
        return real_register(func, *args, **kwargs)

    def tracking_unregister(func):
        unregistered.append(func)
        return real_unregister(func)

    provider = LifecycleProvider()
    with (
        patch("pysolated.orchestrator.atexit.register", tracking_register),
        patch("pysolated.orchestrator.atexit.unregister", tracking_unregister),
    ):
        await run(
            agent=TrivialAgent(),
            sandbox=provider,
            prompt="go",
            display=_NullDisplay(),
        )

    assert registered, "expected an atexit callback to be registered during run()"
    # Every registered callback must have been unregistered when run() returned.
    assert set(registered) <= set(unregistered), (
        "atexit callbacks registered during run() must be unregistered in `finally`"
    )


async def test_each_run_gets_a_fresh_handle() -> None:
    """One frozen provider, three runs → three distinct handles, three closes.

    This is the concurrency-safety guarantee: the provider is reusable; the
    handle is per-run state.
    """
    provider = LifecycleProvider()
    for _ in range(3):
        await run(
            agent=TrivialAgent(),
            sandbox=provider,
            prompt="go",
            display=_NullDisplay(),
        )
    assert len(provider.created) == 3
    assert {h.close_count for h in provider.created} == {1}
