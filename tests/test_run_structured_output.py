"""End-to-end tests for `run(..., output=...)`.

These tests exercise the **integration** between `run()` and the structured-output
module: the two up-front guards (`max_iterations == 1`, opening tag in resolved
prompt), the post-iteration extraction wired to `RunResult.output`, and the
guarantee that the guards run *before* any agent work (so a misconfigured run
never pays for an agent invocation).

The fakes are intentionally permissive — they just replay scripted stream-json
lines — so each test can focus on the contract being asserted.
"""

from __future__ import annotations

import json
from typing import Callable

import pytest
from pydantic import BaseModel

from pysolated import (
    AgentCommandOptions,
    Command,
    ExecResult,
    Output,
    Severity,
    StructuredOutputError,
    parse_session_usage,
    parse_stream_line,
    run,
)


class Answer(BaseModel):
    answer: int


class StrictAnswer(BaseModel):
    model_config = {"extra": "forbid"}
    answer: int


def _assistant(content: list[dict]) -> str:
    """Build a single stream-json assistant line emitting the given content blocks."""
    return json.dumps({"type": "assistant", "message": {"content": content}})


class FakeAgent:
    """Records the prompt it was built with so guard tests can assert the
    agent was (or was NOT) invoked, and replays a scripted set of stream lines.
    """

    name = "fake-agent"
    env: dict[str, str] = {}

    def __init__(self, lines: list[str]) -> None:
        self._lines = lines
        self.build_calls: list[AgentCommandOptions] = []

    def build_command(self, options: AgentCommandOptions) -> Command:
        self.build_calls.append(options)
        return Command(argv=["fake-agent"], stdin=options.prompt)

    def parse_stream_line(self, line: str):
        return parse_stream_line(line)

    def parse_session_usage(self, content: str):
        return parse_session_usage(content)


class FakeSandbox:
    """Replays scripted stdout lines for the agent invocation; answers
    `git rev-parse`/`git rev-list` shells with the canned values pysolated
    expects so the orchestrator's commit-collection path is satisfied.
    """

    name = "fake-sandbox"
    env: dict[str, str] = {}

    def __init__(self, lines: list[str], *, branch: str = "main") -> None:
        self._lines = lines
        self._branch = branch
        self.exec_calls: list[dict] = []

    async def exec(
        self,
        argv: list[str],
        *,
        stdin: str | None = None,
        cwd: str | None = None,
        on_line: Callable[[str], None] | None = None,
    ) -> ExecResult:
        self.exec_calls.append({"argv": list(argv), "stdin": stdin, "cwd": cwd})
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
            exit_code=0,
            stdout="\n".join(self._lines),
            stderr="",
        )


class SilentDisplay:
    """A no-op `Display` — these tests don't care about display output."""

    def intro(self, title: str) -> None: ...
    def status(self, message: str, severity: Severity) -> None: ...
    def text(self, message: str) -> None: ...
    def tool_call(self, name: str, formatted_args: str) -> None: ...
    def summary(self, title: str, rows: dict[str, str]) -> None: ...


def _agent_was_invoked(sandbox: FakeSandbox) -> bool:
    """True iff the sandbox saw the agent argv (any non-git exec call)."""
    return any(
        call["argv"][:1] != ["git"] for call in sandbox.exec_calls
    )


# ---------------------------------------------------------------------------
# Happy path — extraction wired onto the result
# ---------------------------------------------------------------------------


async def test_object_output_set_on_run_result() -> None:
    """`run()` returns the validated Pydantic instance on `RunResult.output`.

    The agent's stdout contains a `<result>{...}</result>` block; pysolated
    pulls it out and validates against the caller's model after the loop ends.
    """
    payload = _assistant(
        [{"type": "text", "text": "Final: <result>{\"answer\": 42}</result>"}]
    )
    agent = FakeAgent([payload])
    sandbox = FakeSandbox([payload])
    result = await run(
        agent=agent,
        sandbox=sandbox,
        prompt="Reply with <result>{...}</result> JSON.",
        display=SilentDisplay(),
        output=Output.object("result", Answer),
    )
    assert isinstance(result.output, Answer)
    assert result.output.answer == 42


async def test_string_output_set_on_run_result() -> None:
    """String mode is end-to-end: `RunResult.output` is the trimmed inner text."""
    payload = _assistant(
        [{"type": "text", "text": "Done: <summary>  the headline  </summary>"}]
    )
    result = await run(
        agent=FakeAgent([payload]),
        sandbox=FakeSandbox([payload]),
        prompt="Reply with <summary>...</summary>.",
        display=SilentDisplay(),
        output=Output.string("summary"),
    )
    assert result.output == "the headline"


async def test_output_none_when_no_output_argument() -> None:
    """Runs without `output=` keep `RunResult.output` as `None`.

    Guards against the structured-output extractor running on every run; the
    feature is opt-in and absence should remain silent.
    """
    payload = _assistant([{"type": "text", "text": "hi"}])
    result = await run(
        agent=FakeAgent([payload]),
        sandbox=FakeSandbox([payload]),
        prompt="say hi",
        display=SilentDisplay(),
    )
    assert result.output is None


# ---------------------------------------------------------------------------
# Up-front guards — must fire before any agent work
# ---------------------------------------------------------------------------


async def test_output_with_max_iterations_above_one_rejected_up_front() -> None:
    """`output=` with `max_iterations != 1` raises before the agent runs.

    Structured output is single-iteration only so the payload unambiguously
    belongs to the iteration that produced it; allowing N > 1 would make it
    ambiguous which pass the payload came from.
    """
    payload = _assistant(
        [{"type": "text", "text": "<result>{\"answer\": 1}</result>"}]
    )
    agent = FakeAgent([payload])
    sandbox = FakeSandbox([payload])
    with pytest.raises(ValueError) as exc:
        await run(
            agent=agent,
            sandbox=sandbox,
            prompt="<result></result>",
            display=SilentDisplay(),
            max_iterations=3,
            output=Output.object("result", Answer),
        )
    assert "max_iterations" in str(exc.value)
    # Critical: the guard fired BEFORE the agent was invoked.
    assert agent.build_calls == []
    assert not _agent_was_invoked(sandbox)


async def test_output_with_missing_opening_tag_in_prompt_rejected_up_front() -> None:
    """A run whose resolved prompt lacks the opening tag is rejected early.

    The check inspects the resolved prompt (after substitution/expansion),
    not the raw template — what matters is whether the agent will actually
    see the tag instruction.
    """
    payload = _assistant(
        [{"type": "text", "text": "<result>{\"answer\": 1}</result>"}]
    )
    agent = FakeAgent([payload])
    sandbox = FakeSandbox([payload])
    with pytest.raises(ValueError) as exc:
        await run(
            agent=agent,
            sandbox=sandbox,
            prompt="please reply with a result",  # no <result> in prompt
            display=SilentDisplay(),
            output=Output.object("result", Answer),
        )
    assert "<result>" in str(exc.value)
    assert agent.build_calls == []
    assert not _agent_was_invoked(sandbox)


async def test_output_guard_passes_when_prompt_contains_opening_tag() -> None:
    """The opening-tag guard considers a literal `<tag>` in the prompt sufficient.

    The prompt typically reads something like "Reply with `<result>{...}</result>`";
    asserting the open tag appears is enough — we don't require well-formed
    XML in the prompt, just evidence the caller asked the agent to emit it.
    """
    payload = _assistant(
        [{"type": "text", "text": "<result>{\"answer\": 5}</result>"}]
    )
    result = await run(
        agent=FakeAgent([payload]),
        sandbox=FakeSandbox([payload]),
        prompt="Reply with <result>{...}</result>",
        display=SilentDisplay(),
        output=Output.object("result", Answer),
    )
    assert isinstance(result.output, Answer)
    assert result.output.answer == 5


# ---------------------------------------------------------------------------
# Validation failures — propagated as `StructuredOutputError`
# ---------------------------------------------------------------------------


async def test_invalid_json_in_tag_raises_structured_output_error() -> None:
    """When the agent emits non-JSON inside the tag, `run()` propagates the
    `StructuredOutputError` from the extractor unchanged.
    """
    payload = _assistant(
        [{"type": "text", "text": "<result>not valid json</result>"}]
    )
    with pytest.raises(StructuredOutputError) as exc:
        await run(
            agent=FakeAgent([payload]),
            sandbox=FakeSandbox([payload]),
            prompt="Reply with <result>{...}</result>",
            display=SilentDisplay(),
            output=Output.object("result", Answer),
        )
    assert exc.value.tag == "result"
    assert exc.value.raw_matched == "not valid json"


async def test_schema_validation_failure_raises_structured_output_error() -> None:
    """JSON that parses but fails the Pydantic model raises with the
    `ValidationError` preserved as the cause.
    """
    payload = _assistant(
        [{"type": "text", "text": '<result>{"answer": 1, "extra": "x"}</result>'}]
    )
    with pytest.raises(StructuredOutputError) as exc:
        await run(
            agent=FakeAgent([payload]),
            sandbox=FakeSandbox([payload]),
            prompt="Reply with <result>{...}</result>",
            display=SilentDisplay(),
            output=Output.object("result", StrictAnswer),
        )
    assert exc.value.tag == "result"
    assert exc.value.cause is not None


async def test_missing_tag_in_stdout_raises_structured_output_error() -> None:
    """If the prompt mentions the tag (so the guard passes) but the agent
    emits no `<tag>...</tag>`, extraction raises a missing-tag error.
    """
    payload = _assistant([{"type": "text", "text": "I forgot to wrap it"}])
    with pytest.raises(StructuredOutputError) as exc:
        await run(
            agent=FakeAgent([payload]),
            sandbox=FakeSandbox([payload]),
            prompt="Reply with <result>{...}</result>",
            display=SilentDisplay(),
            output=Output.object("result", Answer),
        )
    assert exc.value.tag == "result"
    assert exc.value.raw_matched is None
