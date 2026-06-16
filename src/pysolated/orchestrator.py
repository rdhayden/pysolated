"""The orchestrator — the `run()` engine shared by the library and the CLI.

This slice runs a single iteration: resolve the inline prompt verbatim, exec the
agent command through the sandbox, stream events to the display, accumulate
stdout, and return a frozen `RunResult`. It awaits subprocess completion
directly — the racing idle/completion timers are a later slice.
"""

from __future__ import annotations

import os

from .core import (
    AgentCommandOptions,
    AgentProvider,
    Display,
    RunResult,
    SandboxProvider,
    SessionIdEvent,
    StreamEvent,
    TextEvent,
    ToolCallEvent,
    Usage,
)
from .display import TerminalDisplay
from .errors import AgentExecutionError


async def run(
    *,
    agent: AgentProvider,
    sandbox: SandboxProvider,
    prompt: str,
    cwd: str | None = None,
    display: Display | None = None,
    name: str | None = None,
) -> RunResult:
    """Drive an agent once and return a frozen `RunResult`.

    The inline `prompt` is sent to the agent verbatim — no rewriting,
    substitution, or expansion in this slice. `cwd` anchors the run to a repo
    directory (default: the current working directory). `display` is the
    presentation/test-substitution seam (default: a Rich terminal display).
    """
    work_dir = cwd or os.getcwd()
    disp: Display = display if display is not None else TerminalDisplay()

    disp.intro(name or "pysolated")
    branch = await _current_branch(sandbox, work_dir)

    disp.status("Running agent", "info")
    command = agent.build_command(AgentCommandOptions(prompt=prompt))

    stdout_lines: list[str] = []

    def on_line(line: str) -> None:
        stdout_lines.append(line)
        for event in agent.parse_stream_line(line):
            _dispatch_event(disp, event)

    result = await sandbox.exec(
        command.argv, stdin=command.stdin, cwd=work_dir, on_line=on_line
    )
    stdout = "\n".join(stdout_lines)

    if result.exit_code != 0:
        disp.status(f"Agent failed (exit {result.exit_code})", "error")
        raise AgentExecutionError(
            exit_code=result.exit_code, stderr=result.stderr, stdout_tail=stdout
        )

    usage = agent.parse_session_usage(stdout)
    disp.status("Run complete", "success")
    disp.summary("Run summary", _summary_rows(branch, usage))

    return RunResult(iterations=1, stdout=stdout, branch=branch, usage=usage)


async def _current_branch(sandbox: SandboxProvider, cwd: str) -> str:
    """Resolve the current git branch through the sandbox seam.

    Returns "" when the directory is not a git repo (the agent can still run).
    """
    result = await sandbox.exec(
        ["git", "rev-parse", "--abbrev-ref", "HEAD"], cwd=cwd
    )
    if result.exit_code != 0:
        return ""
    return result.stdout.strip()


def _dispatch_event(disp: Display, event: StreamEvent) -> None:
    if isinstance(event, TextEvent):
        disp.text(event.text)
    elif isinstance(event, ToolCallEvent):
        disp.tool_call(event.name, event.args)
    elif isinstance(event, SessionIdEvent):
        # Not surfaced to the display in this slice.
        pass


def _summary_rows(branch: str, usage: Usage | None) -> dict[str, str]:
    rows = {"Branch": branch or "(unknown)"}
    if usage is None:
        rows["Token usage"] = "unavailable"
    else:
        rows["Input tokens"] = str(usage.input_tokens)
        rows["Output tokens"] = str(usage.output_tokens)
        rows["Cache read tokens"] = str(usage.cache_read_input_tokens)
        rows["Cache creation tokens"] = str(usage.cache_creation_input_tokens)
    return rows
