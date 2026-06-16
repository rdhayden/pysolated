"""Tests for the multi-iteration loop and completion-signal early-exit.

Drives the orchestrator with fake `AgentProvider`, `SandboxProvider`, and
`Display` seams — no subprocesses, no wall-clock waits.
"""

from __future__ import annotations

import json
from typing import Callable

from pysolated import (
    AgentCommandOptions,
    Command,
    ExecResult,
    Severity,
    parse_session_usage,
    parse_stream_line,
    run,
)


class ScriptedAgent:
    """Records every build_command call and returns the prompt as stdin."""

    name = "scripted-agent"
    env: dict[str, str] = {}

    def __init__(self) -> None:
        self.prompts: list[str] = []

    def build_command(self, options: AgentCommandOptions) -> Command:
        self.prompts.append(options.prompt)
        return Command(argv=["scripted"], stdin=options.prompt)

    def parse_stream_line(self, line: str):
        return parse_stream_line(line)

    def parse_session_usage(self, content: str):
        return parse_session_usage(content)


class ScriptedSandbox:
    """Emits a different list of stdout lines on each agent invocation.

    Git calls (`rev-parse`, `rev-list`) return canned responses so the
    orchestrator's branch lookup and commit collection don't blow up.
    """

    name = "scripted-sandbox"
    env: dict[str, str] = {}

    def __init__(
        self,
        per_iteration_lines: list[list[str]],
        *,
        branch: str = "feature/x",
        head_sha: str = "deadbeef",
    ) -> None:
        self._per_iteration_lines = per_iteration_lines
        self._branch = branch
        self._head_sha = head_sha
        self._invocation = 0
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
            return ExecResult(exit_code=0, stdout=f"{self._head_sha}\n", stderr="")
        if argv[:2] == ["git", "rev-list"]:
            return ExecResult(exit_code=0, stdout="", stderr="")
        # Agent invocation.
        lines = self._per_iteration_lines[self._invocation]
        self._invocation += 1
        for line in lines:
            if on_line is not None:
                on_line(line)
        return ExecResult(exit_code=0, stdout="\n".join(lines), stderr="")


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


async def test_default_max_iterations_is_one() -> None:
    sandbox = ScriptedSandbox([[_assistant("hi")]])
    agent = ScriptedAgent()
    result = await run(
        agent=agent, sandbox=sandbox, prompt="go", display=RecordingDisplay()
    )
    assert result.iterations == 1
    assert len(agent.prompts) == 1


async def test_runs_all_iterations_when_no_signal_fires() -> None:
    sandbox = ScriptedSandbox(
        [[_assistant("pass 1")], [_assistant("pass 2")], [_assistant("pass 3")]]
    )
    agent = ScriptedAgent()
    result = await run(
        agent=agent,
        sandbox=sandbox,
        prompt="go",
        display=RecordingDisplay(),
        max_iterations=3,
        completion_signal="NEVER-APPEARS",
    )
    assert result.iterations == 3
    assert agent.prompts == ["go", "go", "go"]
    assert result.completion_signal is None
    # stdout accumulates lines from every iteration.
    assert "pass 1" in result.stdout
    assert "pass 2" in result.stdout
    assert "pass 3" in result.stdout


async def test_stops_early_when_completion_signal_appears() -> None:
    sandbox = ScriptedSandbox(
        [
            [_assistant("first pass — still working")],
            [_assistant("done <promise>COMPLETE</promise>")],
            [_assistant("never reached")],
        ]
    )
    agent = ScriptedAgent()
    result = await run(
        agent=agent,
        sandbox=sandbox,
        prompt="go",
        display=RecordingDisplay(),
        max_iterations=5,
    )
    assert result.iterations == 2
    assert result.completion_signal == "<promise>COMPLETE</promise>"
    assert len(agent.prompts) == 2  # third iteration never started


async def test_completion_signal_list_reports_which_one_fired() -> None:
    sandbox = ScriptedSandbox(
        [[_assistant("looking… ALL-DONE here, both markers irrelevant")]]
    )
    agent = ScriptedAgent()
    result = await run(
        agent=agent,
        sandbox=sandbox,
        prompt="go",
        display=RecordingDisplay(),
        max_iterations=3,
        completion_signal=["ALL-DONE", "PARTIAL-DONE"],
    )
    assert result.iterations == 1
    assert result.completion_signal == "ALL-DONE"


async def test_completion_signal_none_when_max_iterations_reached() -> None:
    sandbox = ScriptedSandbox([[_assistant("nothing terminal")]])
    result = await run(
        agent=ScriptedAgent(),
        sandbox=sandbox,
        prompt="go",
        display=RecordingDisplay(),
        max_iterations=1,
        completion_signal="WILL-NOT-MATCH",
    )
    assert result.completion_signal is None
