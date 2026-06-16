"""Orchestrator tests driven by fake seams (no real agent/subprocess)."""

from __future__ import annotations

import json
from typing import Callable

import pytest

from pysolated import (
    AgentCommandOptions,
    Command,
    ExecResult,
    RunResult,
    Severity,
    Usage,
    parse_session_usage,
    parse_stream_line,
    run,
)
from pysolated.errors import AgentExecutionError


class FakeAgent:
    """A stand-in agent: records the prompt, replays a scripted stream."""

    name = "fake-agent"
    env: dict[str, str] = {}

    def __init__(self, lines: list[str]) -> None:
        self._lines = lines
        self.built_options: AgentCommandOptions | None = None

    def build_command(self, options: AgentCommandOptions) -> Command:
        self.built_options = options
        # The fake sandbox replays self._lines regardless of argv.
        return Command(argv=["fake-agent"], stdin=options.prompt)

    def parse_stream_line(self, line: str):
        return parse_stream_line(line)

    def parse_session_usage(self, content: str):
        return parse_session_usage(content)


class FakeSandbox:
    """Replays scripted stdout lines and answers git branch queries."""

    name = "fake-sandbox"
    env: dict[str, str] = {}

    def __init__(self, lines: list[str], *, branch: str = "main", exit_code: int = 0) -> None:
        self._lines = lines
        self._branch = branch
        self._exit_code = exit_code
        self.exec_calls: list[dict] = []

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
            stderr="boom" if self._exit_code else "",
        )


class RecordingDisplay:
    """Records every display call for assertions."""

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


def _assistant(content: list[dict], usage: dict | None = None) -> str:
    message: dict = {"content": content}
    if usage is not None:
        message["usage"] = usage
    return json.dumps({"type": "assistant", "message": message})


USAGE = {
    "input_tokens": 9,
    "cache_creation_input_tokens": 7174,
    "cache_read_input_tokens": 17506,
    "output_tokens": 43,
}


async def test_returns_frozen_run_result() -> None:
    lines = [
        json.dumps({"type": "system", "subtype": "init", "session_id": "s1"}),
        _assistant([{"type": "text", "text": "Hi!"}], usage=USAGE),
    ]
    agent = FakeAgent(lines)
    sandbox = FakeSandbox(lines, branch="feature/x")
    display = RecordingDisplay()

    result = await run(
        agent=agent, sandbox=sandbox, prompt="say hi", cwd="/repo", display=display
    )

    assert isinstance(result, RunResult)
    assert result.iterations == 1
    assert result.branch == "feature/x"
    assert result.usage == Usage(**USAGE)
    assert "Hi!" in result.stdout
    with pytest.raises(Exception):
        result.iterations = 2  # type: ignore[misc]  # frozen


async def test_inline_prompt_passed_verbatim() -> None:
    agent = FakeAgent([])
    sandbox = FakeSandbox([])
    await run(agent=agent, sandbox=sandbox, prompt="  literal {{KEY}} !`cmd`  ", display=RecordingDisplay())
    assert agent.built_options is not None
    assert agent.built_options.prompt == "  literal {{KEY}} !`cmd`  "


async def test_text_and_tool_events_stream_to_display() -> None:
    lines = [
        _assistant(
            [
                {"type": "text", "text": "let me look"},
                {"type": "tool_use", "name": "Bash", "input": {"command": "ls"}},
            ]
        ),
    ]
    display = RecordingDisplay()
    await run(
        agent=FakeAgent(lines), sandbox=FakeSandbox(lines), prompt="go", display=display
    )
    assert ("text", "let me look") in display.calls
    assert ("tool_call", "Bash", "ls") in display.calls
    # The run-end summary surfaces token usage rows.
    summaries = [c for c in display.calls if c[0] == "summary"]
    assert summaries


async def test_summary_shows_token_usage() -> None:
    lines = [_assistant([{"type": "text", "text": "ok"}], usage=USAGE)]
    display = RecordingDisplay()
    await run(agent=FakeAgent(lines), sandbox=FakeSandbox(lines), prompt="go", display=display)
    summary = next(c for c in display.calls if c[0] == "summary")
    rows = summary[2]
    assert rows["Output tokens"] == "43"
    assert rows["Input tokens"] == "9"


async def test_cwd_threaded_to_agent_exec() -> None:
    lines = [_assistant([{"type": "text", "text": "ok"}])]
    sandbox = FakeSandbox(lines)
    await run(agent=FakeAgent(lines), sandbox=sandbox, prompt="go", cwd="/work", display=RecordingDisplay())
    agent_call = next(c for c in sandbox.exec_calls if c["argv"][:2] != ["git", "rev-parse"])
    assert agent_call["cwd"] == "/work"
    assert agent_call["stdin"] == "go"


async def test_nonzero_exit_raises_agent_execution_error() -> None:
    lines = [_assistant([{"type": "text", "text": "partial"}])]
    sandbox = FakeSandbox(lines, exit_code=2)
    with pytest.raises(AgentExecutionError) as exc:
        await run(agent=FakeAgent(lines), sandbox=sandbox, prompt="go", display=RecordingDisplay())
    assert exc.value.exit_code == 2


async def test_nonzero_exit_carries_stderr_tail_for_diagnosis() -> None:
    """A crashed agent's stderr must reach the caller through the exception.

    Without this wiring the error message tells you the exit code but not
    *why* the agent crashed — useless for diagnosis.
    """
    lines = [_assistant([{"type": "text", "text": "halfway through"}])]
    sandbox = FakeSandbox(lines, exit_code=7)
    with pytest.raises(AgentExecutionError) as exc:
        await run(agent=FakeAgent(lines), sandbox=sandbox, prompt="go", display=RecordingDisplay())
    # FakeSandbox writes "boom" to stderr when exit_code != 0.
    assert "boom" in exc.value.stderr_tail
    assert "boom" in str(exc.value)


async def test_nonzero_exit_carries_stdout_tail_when_stderr_empty() -> None:
    """If the agent only logged the crash to stdout, the stdout tail still surfaces.

    Some agents/tools print failures to stdout. The error must still have
    *something* the developer can read.
    """

    class StdoutOnlyFailureSandbox(FakeSandbox):
        async def exec(self, argv, *, stdin=None, cwd=None, on_line=None):  # type: ignore[override]
            self.exec_calls.append({"argv": argv, "stdin": stdin, "cwd": cwd})
            if argv[:2] == ["git", "rev-parse"] and "--abbrev-ref" in argv:
                return ExecResult(exit_code=0, stdout="main\n", stderr="")
            if argv[:2] == ["git", "rev-parse"]:
                return ExecResult(exit_code=0, stdout="deadbeef\n", stderr="")
            if argv[:2] == ["git", "rev-list"]:
                return ExecResult(exit_code=0, stdout="", stderr="")
            for line in self._lines:
                if on_line is not None:
                    on_line(line)
            return ExecResult(exit_code=4, stdout="\n".join(self._lines), stderr="")

    lines = ['{"type":"text","text":"Error: missing config file"}']
    sandbox = StdoutOnlyFailureSandbox(lines)
    with pytest.raises(AgentExecutionError) as exc:
        await run(agent=FakeAgent(lines), sandbox=sandbox, prompt="go", display=RecordingDisplay())
    assert exc.value.exit_code == 4
    assert "missing config file" in exc.value.stdout_tail


async def test_missing_usage_yields_none() -> None:
    lines = [_assistant([{"type": "text", "text": "no usage here"}])]
    result = await run(
        agent=FakeAgent(lines), sandbox=FakeSandbox(lines), prompt="go", display=RecordingDisplay()
    )
    assert result.usage is None
