"""Tests for the three-way timer race inside one agent iteration.

Idle timeout fails the run; completion-grace succeeds with a warning; trailing
output after the signal resets the grace window. Each test runs with very short
injected timeouts so the assertions don't depend on wall-clock thresholds — only
on the relative ordering of timer expiry vs scripted sandbox sleeps.
"""

from __future__ import annotations

import asyncio
import json
from typing import Callable

import pytest

from pysolated import (
    AgentCommandOptions,
    Command,
    ExecResult,
    IdleTimeoutError,
    Severity,
    parse_session_usage,
    parse_stream_line,
    run,
)


class PassthroughAgent:
    name = "passthrough"
    env: dict[str, str] = {}

    def build_command(self, options: AgentCommandOptions) -> Command:
        return Command(argv=["scripted"], stdin=options.prompt)

    def parse_stream_line(self, line: str):
        return parse_stream_line(line)

    def parse_session_usage(self, content: str):
        return parse_session_usage(content)


class TimedSandbox:
    """Streams `(delay, line)` pairs, optionally hanging at the end.

    Sleeping between lines lets a test drive the orchestrator's timers
    deterministically: each line resets `last_line_at`, and a final
    `hang_forever=True` blocks until cancellation so the timer race can decide
    the iteration's fate without a real subprocess.
    """

    name = "timed-sandbox"
    env: dict[str, str] = {}

    def __init__(
        self,
        timed_lines: list[tuple[float, str]],
        *,
        hang_forever: bool = False,
        branch: str = "main",
    ) -> None:
        self._timed_lines = timed_lines
        self._hang = hang_forever
        self._branch = branch
        self.closed = False

    async def create(self, work_dir: str) -> "TimedSandbox":
        return self

    async def close(self) -> None:
        self.closed = True

    async def exec(
        self,
        argv: list[str],
        *,
        stdin: str | None = None,
        cwd: str | None = None,
        on_line: Callable[[str], None] | None = None,
    ) -> ExecResult:
        if argv[:2] == ["git", "rev-parse"] and "--abbrev-ref" in argv:
            return ExecResult(exit_code=0, stdout=f"{self._branch}\n", stderr="")
        if argv[:2] == ["git", "rev-parse"]:
            return ExecResult(exit_code=0, stdout="", stderr="")
        if argv[:2] == ["git", "rev-list"]:
            return ExecResult(exit_code=0, stdout="", stderr="")
        for delay, line in self._timed_lines:
            if delay > 0:
                await asyncio.sleep(delay)
            if on_line is not None:
                on_line(line)
        if self._hang:
            await asyncio.Event().wait()  # cancelled by the orchestrator
        return ExecResult(
            exit_code=0,
            stdout="\n".join(line for _, line in self._timed_lines),
            stderr="",
        )


class RecordingDisplay:
    def __init__(self) -> None:
        self.calls: list[tuple] = []

    def intro(self, title: str) -> None:
        self.calls.append(("intro", title))

    def status(self, message: str, severity: Severity) -> None:
        self.calls.append(("status", message, severity))

    def text(self, message: str) -> None:
        self.calls.append(("text", message))

    def tool_call(self, name: str, formatted_args: str) -> None:
        self.calls.append(("tool_call", name, formatted_args))

    def summary(self, title: str, rows: dict[str, str]) -> None:
        self.calls.append(("summary", title, rows))


def _assistant(text: str) -> str:
    return json.dumps(
        {"type": "assistant", "message": {"content": [{"type": "text", "text": text}]}}
    )


async def test_idle_timeout_fails_when_agent_emits_nothing() -> None:
    sandbox = TimedSandbox([], hang_forever=True)
    display = RecordingDisplay()
    with pytest.raises(IdleTimeoutError) as exc:
        await run(
            agent=PassthroughAgent(),
            sandbox=sandbox,
            prompt="go",
            display=display,
            idle_timeout_seconds=0.05,
            completion_timeout_seconds=10.0,
            idle_warning_interval_seconds=10.0,
        )
    assert exc.value.timeout_seconds == 0.05


async def test_idle_timer_resets_on_each_line_before_signal() -> None:
    # Three lines each below the idle threshold — total elapsed exceeds the
    # idle timeout, but no single gap does. The run should succeed (no signal
    # required because the sandbox returns normally after the last line).
    sandbox = TimedSandbox(
        [(0.04, _assistant("a")), (0.04, _assistant("b")), (0.04, _assistant("c"))],
        hang_forever=False,
    )
    result = await run(
        agent=PassthroughAgent(),
        sandbox=sandbox,
        prompt="go",
        display=RecordingDisplay(),
        idle_timeout_seconds=0.08,
        completion_timeout_seconds=10.0,
        idle_warning_interval_seconds=10.0,
    )
    assert "a" in result.stdout and "b" in result.stdout and "c" in result.stdout


async def test_completion_grace_succeeds_when_signal_then_hang() -> None:
    sandbox = TimedSandbox(
        [(0.0, _assistant("doing <promise>COMPLETE</promise>"))], hang_forever=True
    )
    result = await run(
        agent=PassthroughAgent(),
        sandbox=sandbox,
        prompt="go",
        display=RecordingDisplay(),
        idle_timeout_seconds=10.0,
        completion_timeout_seconds=0.05,
        idle_warning_interval_seconds=10.0,
    )
    assert result.completion_signal == "<promise>COMPLETE</promise>"
    assert result.iterations == 1


async def test_completion_grace_resets_on_trailing_lines_post_signal() -> None:
    # Signal in line 1; trailing line 2 arrives 40ms later (under the 50ms
    # grace) and resets the timer; grace then expires another 50ms after that.
    # The trailing line must appear in stdout — that's the whole point of
    # resetting the grace window on post-signal output.
    trailing = _assistant("trailing usage event after signal")
    sandbox = TimedSandbox(
        [(0.0, _assistant("done <promise>COMPLETE</promise>")), (0.04, trailing)],
        hang_forever=True,
    )
    result = await run(
        agent=PassthroughAgent(),
        sandbox=sandbox,
        prompt="go",
        display=RecordingDisplay(),
        idle_timeout_seconds=10.0,
        completion_timeout_seconds=0.05,
        idle_warning_interval_seconds=10.0,
    )
    assert result.completion_signal == "<promise>COMPLETE</promise>"
    assert "trailing usage event after signal" in result.stdout


async def test_idle_warning_emitted_while_waiting() -> None:
    display = RecordingDisplay()
    sandbox = TimedSandbox([], hang_forever=True)
    with pytest.raises(IdleTimeoutError):
        await run(
            agent=PassthroughAgent(),
            sandbox=sandbox,
            prompt="go",
            display=display,
            idle_timeout_seconds=0.20,
            completion_timeout_seconds=10.0,
            idle_warning_interval_seconds=0.05,
        )
    # At least one idle-warning status message landed on the display before the
    # idle error fired.
    warn_messages = [
        c[1]
        for c in display.calls
        if c[0] == "status" and c[2] == "warn" and "idle" in c[1]
    ]
    assert warn_messages, "expected an idle warning before idle-timeout error"
