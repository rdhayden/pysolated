"""Tests for `ResultEvent` and the orchestrator's stderr-empty fallback.

`ResultEvent` is a narrow in-band error channel for agents that surface auth /
rate-limit / API errors on stdout (Codex's `{type:"error"}` now, others later).
The orchestrator tracks the last `ResultEvent` of an iteration and uses it for
exactly one purpose: when the agent exits non-zero with empty stderr, its text
becomes the `AgentExecutionError` message. It is also displayed live at error
severity. It MUST NOT feed `RunResult.text` / prose, completion-signal matching,
or structured-output extraction.
"""

from __future__ import annotations

import json
from typing import Callable

import pytest

from pysolated import (
    AgentCommandOptions,
    Command,
    ExecResult,
    ResultEvent,
    Severity,
    StreamEvent,
    TextEvent,
    Usage,
    parse_session_usage,
    run,
)
from pysolated.errors import AgentExecutionError


class ScriptedAgent:
    """A fake agent whose `parse_stream_line` decodes each line as one event.

    Lines beginning with `RESULT:` become `ResultEvent(<rest>)`; lines starting
    with `TEXT:` become `TextEvent(<rest>)`; everything else yields no events.
    Keeps the tests free of stream-json plumbing so they read as behavioural
    specifications.
    """

    name = "scripted-agent"
    env: dict[str, str] = {}

    def __init__(self, lines: list[str]) -> None:
        self._lines = lines
        self.built_options: AgentCommandOptions | None = None

    def build_command(self, options: AgentCommandOptions) -> Command:
        self.built_options = options
        return Command(argv=["scripted-agent"], stdin=options.prompt)

    def parse_stream_line(self, line: str) -> list[StreamEvent]:
        if line.startswith("RESULT:"):
            return [ResultEvent(line[len("RESULT:") :])]
        if line.startswith("TEXT:"):
            return [TextEvent(line[len("TEXT:") :])]
        return []

    def parse_session_usage(self, content: str) -> Usage | None:
        return parse_session_usage(content)


class ScriptedSandbox:
    """Replays scripted lines, answers git probes, and configures exit code/stderr."""

    name = "scripted-sandbox"
    env: dict[str, str] = {}

    def __init__(
        self,
        lines: list[str],
        *,
        branch: str = "main",
        exit_code: int = 0,
        stderr: str = "",
    ) -> None:
        self._lines = lines
        self._branch = branch
        self._exit_code = exit_code
        self._stderr = stderr
        self.exec_calls: list[dict] = []
        self.closed = False

    async def create(self, work_dir: str) -> "ScriptedSandbox":
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
        self.exec_calls.append({"argv": argv, "stdin": stdin, "cwd": cwd})
        if argv[:2] == ["git", "rev-parse"] and "--abbrev-ref" in argv:
            return ExecResult(exit_code=0, stdout=f"{self._branch}\n", stderr="")
        if argv[:2] == ["git", "rev-parse"]:
            return ExecResult(exit_code=0, stdout="deadbeef\n", stderr="")
        if argv[:2] == ["git", "rev-list"]:
            return ExecResult(exit_code=0, stdout="", stderr="")
        for line in self._lines:
            if on_line is not None:
                on_line(line)
        return ExecResult(
            exit_code=self._exit_code,
            stdout="\n".join(self._lines),
            stderr=self._stderr,
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


def test_result_event_is_a_stream_event() -> None:
    """`ResultEvent(text)` is a member of the `StreamEvent` union."""
    event: StreamEvent = ResultEvent(text="Error: invalid API key")
    assert isinstance(event, ResultEvent)
    assert event.text == "Error: invalid API key"


def test_result_event_is_frozen() -> None:
    """Stream events are frozen value types — `ResultEvent` follows suit."""
    event = ResultEvent(text="boom")
    with pytest.raises(Exception):
        event.text = "different"  # type: ignore[misc]


async def test_nonzero_exit_with_empty_stderr_surfaces_last_result_event() -> None:
    """The stderr-empty fallback: the in-band error becomes the exception message."""
    sandbox = ScriptedSandbox(
        ["RESULT:Error: rate limit exceeded"], exit_code=1, stderr=""
    )
    agent = ScriptedAgent(["RESULT:Error: rate limit exceeded"])
    with pytest.raises(AgentExecutionError) as exc:
        await run(
            agent=agent,
            sandbox=sandbox,
            prompt="go",
            display=RecordingDisplay(),
        )
    assert "rate limit exceeded" in str(exc.value)


async def test_last_result_event_wins_for_fallback() -> None:
    """When multiple `ResultEvent`s appear, the last one is used in the error."""
    lines = ["RESULT:Warning: degraded", "RESULT:Error: terminal"]
    sandbox = ScriptedSandbox(lines, exit_code=1, stderr="")
    agent = ScriptedAgent(lines)
    with pytest.raises(AgentExecutionError) as exc:
        await run(
            agent=agent,
            sandbox=sandbox,
            prompt="go",
            display=RecordingDisplay(),
        )
    message = str(exc.value)
    assert "Error: terminal" in message
    assert "Warning: degraded" not in message


async def test_stderr_takes_precedence_over_result_event() -> None:
    """When stderr is populated, it wins — `ResultEvent` is only a fallback."""
    lines = ["RESULT:in-band-message"]
    sandbox = ScriptedSandbox(lines, exit_code=1, stderr="real-stderr-message")
    agent = ScriptedAgent(lines)
    with pytest.raises(AgentExecutionError) as exc:
        await run(
            agent=agent,
            sandbox=sandbox,
            prompt="go",
            display=RecordingDisplay(),
        )
    message = str(exc.value)
    assert "real-stderr-message" in message


async def test_result_event_displays_at_error_severity() -> None:
    """`ResultEvent` is surfaced live via `display.status(..., "error")`."""
    lines = ["TEXT:hi", "RESULT:Error: missing token"]
    display = RecordingDisplay()
    sandbox = ScriptedSandbox(lines, exit_code=0)
    await run(
        agent=ScriptedAgent(lines),
        sandbox=sandbox,
        prompt="go",
        display=display,
    )
    error_statuses = [c for c in display.calls if c[0] == "status" and c[2] == "error"]
    assert any("missing token" in c[1] for c in error_statuses)


async def test_result_event_does_not_feed_run_result_text() -> None:
    """`RunResult.text` is the agent's prose; `ResultEvent` text must stay out of it."""
    lines = ["TEXT:final answer", "RESULT:Error: should not appear in text"]
    sandbox = ScriptedSandbox(lines, exit_code=0)
    result = await run(
        agent=ScriptedAgent(lines),
        sandbox=sandbox,
        prompt="go",
        display=RecordingDisplay(),
    )
    assert "final answer" in result.text
    assert "Error: should not appear in text" not in result.text


async def test_result_event_does_not_trigger_completion_signal() -> None:
    """Completion-signal matching runs against prose only, never `ResultEvent`."""
    lines = ["RESULT:<promise>COMPLETE</promise>", "TEXT:still working"]
    sandbox = ScriptedSandbox(lines, exit_code=0)
    result = await run(
        agent=ScriptedAgent(lines),
        sandbox=sandbox,
        prompt="go",
        display=RecordingDisplay(),
        max_iterations=1,
    )
    # The signal-bearing line was a ResultEvent, not prose — matching ignores it.
    assert result.completion_signal is None


async def test_result_event_does_not_break_existing_claude_path() -> None:
    """The Claude `stream-json` path is byte-for-byte unchanged.

    A run that only emits `TextEvent`s (Claude's normal case) still produces the
    same `RunResult.text` and reaches the same display calls.
    """
    USAGE = {
        "input_tokens": 9,
        "cache_creation_input_tokens": 7174,
        "cache_read_input_tokens": 17506,
        "output_tokens": 43,
    }

    class ClaudeFakeAgent:
        name = "fake-agent"
        env: dict[str, str] = {}

        def build_command(self, options: AgentCommandOptions) -> Command:
            return Command(argv=["fake"], stdin=options.prompt)

        def parse_stream_line(self, line: str) -> list[StreamEvent]:
            from pysolated import parse_stream_line as p

            return p(line)

        def parse_session_usage(self, content: str) -> Usage | None:
            return parse_session_usage(content)

    lines = [
        json.dumps(
            {
                "type": "assistant",
                "message": {
                    "content": [{"type": "text", "text": "All done"}],
                    "usage": USAGE,
                },
            }
        ),
    ]
    sandbox = ScriptedSandbox(lines, exit_code=0)
    display = RecordingDisplay()
    result = await run(
        agent=ClaudeFakeAgent(),
        sandbox=sandbox,
        prompt="go",
        display=display,
    )
    assert result.text == "All done"
    assert result.usage == Usage(**USAGE)
