"""Orchestrator-level tests for prompt-pipeline wiring inside `run()`.

The unit-level coverage of substitution and expansion lives in
`test_prompt_pipeline.py`. These tests assert that `run()` *uses* the pipeline
correctly: `prompt_file` is loaded and substituted, the current branch is
injected as a built-in argument without the caller wiring it, and the inline
path still bypasses both stages — so this slice doesn't regress the inline
guarantee.
"""

from __future__ import annotations

import json
from pathlib import Path
from typing import Callable

import pytest

from pysolated import (
    AgentCommandOptions,
    Command,
    ExecResult,
    PromptArgumentError,
    Severity,
    parse_session_usage,
    parse_stream_line,
    run,
)


class FakeAgent:
    """Mirrors `tests/test_orchestrator.FakeAgent` — records the stdin prompt."""

    name = "fake-agent"
    env: dict[str, str] = {}

    def __init__(self, lines: list[str]) -> None:
        self._lines = lines
        self.built_options: AgentCommandOptions | None = None

    def build_command(self, options: AgentCommandOptions) -> Command:
        self.built_options = options
        return Command(argv=["fake-agent"], stdin=options.prompt)

    def parse_stream_line(self, line: str):
        return parse_stream_line(line)

    def parse_session_usage(self, content: str):
        return parse_session_usage(content)


class FakeSandbox:
    """Recognizes git rev-parse/rev-list, `sh -c` (prompt expansion), agent run."""

    name = "fake-sandbox"
    env: dict[str, str] = {}

    def __init__(
        self,
        lines: list[str],
        *,
        branch: str = "main",
        shell_script: dict[str, ExecResult] | None = None,
    ) -> None:
        self._lines = lines
        self._branch = branch
        self._shell_script = shell_script or {}
        self.shell_calls: list[str] = []
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
        if argv[:2] == ["sh", "-c"]:
            command = argv[2]
            self.shell_calls.append(command)
            if command in self._shell_script:
                return self._shell_script[command]
            return ExecResult(exit_code=0, stdout="", stderr="")
        # Agent invocation: stream scripted lines.
        for line in self._lines:
            if on_line is not None:
                on_line(line)
        return ExecResult(exit_code=0, stdout="\n".join(self._lines), stderr="")


class _NullDisplay:
    def intro(self, title: str) -> None: ...
    def status(self, message: str, severity: Severity) -> None: ...
    def text(self, message: str) -> None: ...
    def tool_call(self, name: str, formatted_args: str) -> None: ...
    def summary(self, title: str, rows: dict[str, str]) -> None: ...


def _assistant_line(text: str) -> str:
    return json.dumps(
        {"type": "assistant", "message": {"content": [{"type": "text", "text": text}]}}
    )


async def test_prompt_file_template_is_substituted_and_expanded(
    tmp_path: Path,
) -> None:
    template = tmp_path / "p.txt"
    template.write_text(
        "Refactor in {{area}} on branch {{branch}}. Last: !`git log -1`",
        encoding="utf-8",
    )
    lines = [_assistant_line("ok")]
    agent = FakeAgent(lines)
    sandbox = FakeSandbox(
        lines,
        branch="feat/x",
        shell_script={
            "git log -1": ExecResult(exit_code=0, stdout="abc fix\n", stderr=""),
        },
    )

    await run(
        agent=agent,
        sandbox=sandbox,
        prompt_file=template,
        prompt_args={"area": "auth"},
        cwd="/repo",
        display=_NullDisplay(),
    )

    # The fully-resolved prompt is what the agent receives on stdin.
    assert agent.built_options is not None
    assert (
        agent.built_options.prompt
        == "Refactor in auth on branch feat/x. Last: abc fix"
    )
    # Expansion was routed through the sandbox seam.
    assert sandbox.shell_calls == ["git log -1"]


async def test_built_in_branch_arg_does_not_need_explicit_wiring(
    tmp_path: Path,
) -> None:
    template = tmp_path / "p.txt"
    template.write_text("Branch is {{branch}}.", encoding="utf-8")
    lines = [_assistant_line("ok")]
    agent = FakeAgent(lines)
    sandbox = FakeSandbox(lines, branch="main")

    await run(
        agent=agent,
        sandbox=sandbox,
        prompt_file=template,
        # No prompt_args supplied — the orchestrator must inject `branch`.
        display=_NullDisplay(),
    )

    assert agent.built_options is not None
    assert agent.built_options.prompt == "Branch is main."


async def test_inline_prompt_still_bypasses_pipeline_after_wiring(
    tmp_path: Path,
) -> None:
    lines = [_assistant_line("ok")]
    agent = FakeAgent(lines)
    sandbox = FakeSandbox(lines)

    await run(
        agent=agent,
        sandbox=sandbox,
        prompt="  literal {{KEY}} !`rm -rf /`  ",
        display=_NullDisplay(),
    )

    assert agent.built_options is not None
    # Inline passes through verbatim.
    assert agent.built_options.prompt == "  literal {{KEY}} !`rm -rf /`  "
    # No shell expansion ran — the `!`...` `` literal was NOT executed.
    assert sandbox.shell_calls == []


async def test_inline_prompt_with_args_rejected_by_run(tmp_path: Path) -> None:
    lines = [_assistant_line("ok")]
    with pytest.raises(PromptArgumentError):
        await run(
            agent=FakeAgent(lines),
            sandbox=FakeSandbox(lines),
            prompt="hi",
            prompt_args={"who": "robin"},
            display=_NullDisplay(),
        )


async def test_user_arg_overriding_built_in_rejected_by_run(
    tmp_path: Path,
) -> None:
    template = tmp_path / "p.txt"
    template.write_text("{{branch}}", encoding="utf-8")
    lines = [_assistant_line("ok")]
    with pytest.raises(PromptArgumentError):
        await run(
            agent=FakeAgent(lines),
            sandbox=FakeSandbox(lines),
            prompt_file=template,
            prompt_args={"branch": "evil"},
            display=_NullDisplay(),
        )
