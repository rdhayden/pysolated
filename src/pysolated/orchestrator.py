"""The orchestrator — the `run()` engine shared by the library and the CLI.

Each iteration races four conditions while the sandbox streams the agent's
stdout: an **idle timeout** (no line for too long → fail the run), a
**completion timeout** (a grace window that engages once the configured
completion signal appears → succeed-with-warning on expiry), the agent
subprocess exiting, and an **abort** signal (`signal: asyncio.Event` on
`run()`). Firing the abort cancels the in-flight `sandbox.exec`, which on
`no_sandbox` kills the host subprocess; `run()` then raises
`asyncio.CancelledError`. The CLI installs a SIGINT handler that sets that
event, so Ctrl-C maps cleanly onto cancellation instead of tearing through
asyncio mid-await. Timeouts are injected as parameters so tests can drive
them with deterministic short values.

The outer loop runs `1..max_iterations` and returns early with the matched
signal the moment one fires. `RunResult.completion_signal` reports which signal
fired (or `None` when max-iterations was reached). `RunResult.commits` is the
`rev-list <pre-run HEAD>..HEAD` collected once, after the loop exits.
"""

from __future__ import annotations

import asyncio
import os
import time
from dataclasses import dataclass

from pathlib import Path

from .completion import match_completion_signal
from .core import (
    AgentCommandOptions,
    AgentProvider,
    Display,
    ExecResult,
    RunResult,
    SandboxProvider,
    SessionIdEvent,
    StreamEvent,
    TextEvent,
    ToolCallEvent,
    Usage,
)
from .display import FileDisplay, TerminalDisplay
from .errors import AgentExecutionError, IdleTimeoutError
from .prompts import resolve_prompt
from .structured_output import (
    OutputDefinition,
    extract_structured_output,
)

DEFAULT_COMPLETION_SIGNAL = "<promise>COMPLETE</promise>"
DEFAULT_IDLE_TIMEOUT_SECONDS = 600.0
DEFAULT_COMPLETION_TIMEOUT_SECONDS = 60.0
DEFAULT_IDLE_WARNING_INTERVAL_SECONDS = 60.0


@dataclass
class _IterationOutcome:
    """What one iteration produced — stdout, decoded prose, and how it ended.

    `agent_text` is the agent's own decoded prose (concatenated `TextEvent`
    payloads). It's carried alongside the raw `stdout` so post-iteration
    consumers — completion-signal matching and structured-output extraction —
    operate on the *agent's words*, not on the JSON-encoded stream-json
    wrapper. Without this split, a `<tag>` whose inner JSON contains quotes
    would arrive with backslash-escaped quotes (broken JSON) when scanned.
    """

    stdout: str
    agent_text: str
    matched_signal: str | None
    grace_expired: bool


async def run(
    *,
    agent: AgentProvider,
    sandbox: SandboxProvider,
    prompt: str | None = None,
    prompt_file: str | Path | None = None,
    prompt_args: dict[str, str] | None = None,
    cwd: str | None = None,
    display: Display | None = None,
    log_file: str | Path | None = None,
    name: str | None = None,
    max_iterations: int = 1,
    completion_signal: str | list[str] | tuple[str, ...] = DEFAULT_COMPLETION_SIGNAL,
    idle_timeout_seconds: float = DEFAULT_IDLE_TIMEOUT_SECONDS,
    completion_timeout_seconds: float = DEFAULT_COMPLETION_TIMEOUT_SECONDS,
    idle_warning_interval_seconds: float = DEFAULT_IDLE_WARNING_INTERVAL_SECONDS,
    output: OutputDefinition | None = None,
    signal: asyncio.Event | None = None,
) -> RunResult:
    """Drive an agent through `max_iterations` and return a frozen `RunResult`.

    Exactly one of `prompt` and `prompt_file` must be supplied. An inline
    `prompt` is sent to the agent verbatim — no rewriting, substitution, or
    expansion; supplying `prompt_args` alongside it is rejected up front. A
    `prompt_file` is loaded as a template: `{{KEY}}` placeholders are
    substituted from `prompt_args` overlaid on built-in arguments (the current
    branch is always available), then `` !`command` `` shell expressions are
    evaluated via the sandbox seam. A non-zero shell exit fails the run
    before any iteration starts.

    `cwd` anchors the run (default: current working directory). `display` is
    the presentation / test-substitution seam.

    The loop stops early the moment a `completion_signal` substring appears in
    the agent's own assistant prose (tool inputs/outputs the agent reads are
    never matched). `idle_timeout_seconds` fails the run if no output
    arrives; `completion_timeout_seconds` is the grace window that takes over
    once the signal is seen. Both are injected so tests can drive them with
    short, deterministic values.

    `output` (optional) requests **structured output**: after the single
    iteration completes, the resolved stdout is scanned for `<tag>...</tag>`
    and the payload is returned on `RunResult.output`. Two guards run before
    any agent work, so a misconfigured call fails fast:
    `max_iterations != 1` is rejected (the payload must unambiguously belong
    to one iteration) and the resolved prompt must contain the configured
    opening tag (catches a missing output instruction without paying for a
    run). A payload that fails JSON parse or schema validation raises
    `StructuredOutputError`.

    `signal` (optional) is an `asyncio.Event` the caller can set to abort the
    run mid-flight. When set during an iteration, the in-flight
    `sandbox.exec` is cancelled (on `no_sandbox`, that kills the host
    subprocess) and `run()` raises `asyncio.CancelledError`. Setting the
    event between iterations stops the outer loop before the next iteration
    starts. The CLI installs a SIGINT handler that sets this event so Ctrl-C
    aborts cleanly.
    """
    if max_iterations < 1:
        raise ValueError("max_iterations must be >= 1")
    if output is not None and max_iterations != 1:
        raise ValueError(
            "structured output requires max_iterations == 1 "
            f"(got max_iterations={max_iterations})"
        )
    if display is not None and log_file is not None:
        raise ValueError(
            "display= and log_file= are mutually exclusive — pass one or the other"
        )

    work_dir = cwd or os.getcwd()
    log_file_path: str | None = str(log_file) if log_file is not None else None
    disp: Display
    if display is not None:
        disp = display
    elif log_file is not None:
        disp = FileDisplay(log_file, name=name)
    else:
        disp = TerminalDisplay(name=name)
    signals = _normalize_signals(completion_signal)

    disp.intro(name or "pysolated")
    branch = await _current_branch(sandbox, work_dir)
    pre_run_head = await _head_sha(sandbox, work_dir)

    resolved_prompt = await resolve_prompt(
        inline=prompt,
        file=prompt_file,
        user_args=prompt_args,
        built_in_args=_built_in_prompt_args(branch),
        executor=_make_prompt_executor(sandbox, work_dir),
    )
    if output is not None:
        opening_tag = f"<{output.tag}>"
        if opening_tag not in resolved_prompt:
            # Caller-owned prompt: pysolated does NOT inject an instruction
            # describing the tag, so a missing opening tag almost certainly
            # means the prompt was written without the required instruction.
            # Fail before the agent runs to avoid paying for guaranteed failure.
            raise ValueError(
                f"structured output requires the resolved prompt to contain "
                f"the opening tag {opening_tag!r}; none was found"
            )

    accumulated_stdout: list[str] = []
    accumulated_prose: list[str] = []
    matched_signal: str | None = None
    iterations_done = 0

    for iteration_num in range(1, max_iterations + 1):
        if signal is not None and signal.is_set():
            disp.status("Run aborted by signal", "warn")
            raise asyncio.CancelledError("run aborted by signal")
        iterations_done = iteration_num
        disp.status(f"Iteration {iteration_num}/{max_iterations}", "info")

        outcome = await _run_iteration(
            agent=agent,
            sandbox=sandbox,
            prompt=resolved_prompt,
            cwd=work_dir,
            completion_signals=signals,
            idle_timeout_seconds=idle_timeout_seconds,
            completion_timeout_seconds=completion_timeout_seconds,
            idle_warning_interval_seconds=idle_warning_interval_seconds,
            display=disp,
            abort_signal=signal,
        )
        accumulated_stdout.append(outcome.stdout)
        accumulated_prose.append(outcome.agent_text)
        if outcome.matched_signal is not None:
            matched_signal = outcome.matched_signal
            break

    stdout = "\n".join(accumulated_stdout)
    prose = "\n".join(accumulated_prose)
    commits = await _commits_since(sandbox, work_dir, pre_run_head)
    usage = agent.parse_session_usage(stdout)

    # Extract against the agent's decoded prose, not the raw stream-json.
    # The tag's inner JSON arrives JSON-escaped (quotes backslash-escaped) on
    # the wire; only the decoded text events carry it in its original shape.
    extracted_output = (
        extract_structured_output(prose, output) if output is not None else None
    )

    disp.status("Run complete", "success")
    disp.summary(
        "Run summary", _summary_rows(branch, usage, matched_signal, commits)
    )

    return RunResult(
        iterations=iterations_done,
        stdout=stdout,
        branch=branch,
        usage=usage,
        completion_signal=matched_signal,
        commits=commits,
        output=extracted_output,
        log_file_path=log_file_path,
    )


def _normalize_signals(
    signal: str | list[str] | tuple[str, ...],
) -> tuple[str, ...]:
    if isinstance(signal, str):
        return (signal,)
    return tuple(signal)


def _built_in_prompt_args(branch: str) -> dict[str, str]:
    """The argument set pysolated always injects into prompt templates.

    Minimum for v1: the current branch (empty string when not in a git repo,
    matching `RunResult.branch`). Adding more built-ins later is purely
    additive — callers cannot shadow these keys.
    """
    return {"branch": branch}


def _make_prompt_executor(sandbox: SandboxProvider, cwd: str):
    """Wrap the sandbox seam as the executor used by `expand_shell_expressions`.

    Each `` !`command` `` runs through `sh -c` so the user can write the
    natural shell syntax they would type at a terminal (pipes, redirects,
    quoting). The sandbox seam is otherwise the same one the agent uses, so a
    Docker sandbox later will execute prompt commands inside the container.
    """

    async def execute(command: str) -> ExecResult:
        return await sandbox.exec(["sh", "-c", command], cwd=cwd)

    return execute


async def _run_iteration(
    *,
    agent: AgentProvider,
    sandbox: SandboxProvider,
    prompt: str,
    cwd: str,
    completion_signals: tuple[str, ...],
    idle_timeout_seconds: float,
    completion_timeout_seconds: float,
    idle_warning_interval_seconds: float,
    display: Display,
    abort_signal: asyncio.Event | None = None,
) -> _IterationOutcome:
    """Stream one agent invocation, race the three timeout/completion conditions.

    Returns the iteration's stdout and which signal (if any) matched. Raises
    `IdleTimeoutError` when the idle timer fires before any signal; the
    completion-grace timer instead returns successfully with `grace_expired=True`
    and a warning on the display. When `abort_signal` is set mid-iteration the
    exec/timer tasks are cancelled and `asyncio.CancelledError` is raised so the
    subprocess kill path (see `no_sandbox`) runs and `run()` stops promptly.
    """
    accumulated: list[str] = []
    agent_text: list[str] = []
    state: dict = {
        "matched_signal": None,
        "last_line_at": time.monotonic(),
        "signal_seen_at": None,
        "warning_anchor_at": time.monotonic(),
    }
    line_event = asyncio.Event()

    def on_line(line: str) -> None:
        now = time.monotonic()
        accumulated.append(line)
        state["last_line_at"] = now
        state["warning_anchor_at"] = now
        for event in agent.parse_stream_line(line):
            if isinstance(event, TextEvent):
                agent_text.append(event.text)
            _dispatch_event(display, event)
        # Match only against the agent's own prose — never tool inputs/outputs.
        # Otherwise the agent merely reading a file that quotes the signal
        # (this repo's README, source, and docs all do) would trip completion.
        if state["matched_signal"] is None:
            matched = match_completion_signal(
                "\n".join(agent_text), completion_signals
            )
            if matched is not None:
                state["matched_signal"] = matched
                state["signal_seen_at"] = now
                display.status(
                    f"Completion signal seen ({matched!r}); "
                    f"grace window {completion_timeout_seconds:g}s",
                    "info",
                )
        line_event.set()

    command = agent.build_command(AgentCommandOptions(prompt=prompt))
    exec_task = asyncio.create_task(
        sandbox.exec(command.argv, stdin=command.stdin, cwd=cwd, on_line=on_line)
    )
    timer_outcome: dict = {"kind": "running"}
    timer_task = asyncio.create_task(
        _timer_loop(
            state=state,
            line_event=line_event,
            timer_outcome=timer_outcome,
            idle_timeout_seconds=idle_timeout_seconds,
            completion_timeout_seconds=completion_timeout_seconds,
            idle_warning_interval_seconds=idle_warning_interval_seconds,
            display=display,
        )
    )
    abort_task: asyncio.Task | None = None
    race_tasks: list[asyncio.Task] = [exec_task, timer_task]
    if abort_signal is not None:
        abort_task = asyncio.create_task(abort_signal.wait())
        race_tasks.append(abort_task)

    done, pending = await asyncio.wait(
        race_tasks, return_when=asyncio.FIRST_COMPLETED
    )
    for task in pending:
        task.cancel()
    for task in pending:
        try:
            await task
        except (asyncio.CancelledError, Exception):
            pass

    stdout = "\n".join(accumulated)
    prose = "\n".join(agent_text)

    if abort_task is not None and abort_task in done:
        display.status("Run aborted by signal", "warn")
        raise asyncio.CancelledError("run aborted by signal")

    if exec_task in done:
        result = exec_task.result()
        if result.exit_code != 0:
            display.status(f"Agent failed (exit {result.exit_code})", "error")
            raise AgentExecutionError(
                exit_code=result.exit_code,
                stderr=result.stderr,
                stdout_tail=stdout,
            )
        return _IterationOutcome(
            stdout=stdout,
            agent_text=prose,
            matched_signal=state["matched_signal"],
            grace_expired=False,
        )

    # Timer decided the iteration's fate first.
    kind = timer_outcome["kind"]
    if kind == "idle":
        display.status(
            f"Idle timeout — no output for {idle_timeout_seconds:g}s", "error"
        )
        raise IdleTimeoutError(
            timeout_seconds=idle_timeout_seconds, stdout_tail=stdout
        )
    if kind == "grace":
        display.status(
            "Completion grace expired — agent still hanging, forcing success",
            "warn",
        )
        return _IterationOutcome(
            stdout=stdout,
            agent_text=prose,
            matched_signal=state["matched_signal"],
            grace_expired=True,
        )
    # Unreachable in practice — defensive default.
    return _IterationOutcome(
        stdout=stdout,
        agent_text=prose,
        matched_signal=state["matched_signal"],
        grace_expired=False,
    )


async def _timer_loop(
    *,
    state: dict,
    line_event: asyncio.Event,
    timer_outcome: dict,
    idle_timeout_seconds: float,
    completion_timeout_seconds: float,
    idle_warning_interval_seconds: float,
    display: Display,
) -> None:
    """Watch the idle/completion clocks; finish when one fires.

    Waits on `line_event`; every set indicates a line arrived, which restarts
    the relevant timer (idle if no signal yet, completion-grace after the
    signal). On expiry, records the kind in `timer_outcome` and returns.
    """
    while True:
        now = time.monotonic()
        if state["matched_signal"] is None:
            idle_deadline = state["last_line_at"] + idle_timeout_seconds
            warn_deadline = (
                state["warning_anchor_at"] + idle_warning_interval_seconds
            )
            next_wake = min(idle_deadline, warn_deadline)
        else:
            next_wake = state["signal_seen_at"] + completion_timeout_seconds

        wait_for = max(0.0, next_wake - now)
        line_event.clear()
        try:
            await asyncio.wait_for(line_event.wait(), timeout=wait_for)
            # A line arrived (or the warning fired and reset by us) — re-evaluate.
            continue
        except asyncio.TimeoutError:
            pass

        now = time.monotonic()
        if state["matched_signal"] is None:
            if now - state["last_line_at"] >= idle_timeout_seconds:
                timer_outcome["kind"] = "idle"
                return
            elapsed = now - state["last_line_at"]
            minutes = max(1, int(elapsed // 60))
            display.status(f"agent idle for {minutes} minutes", "warn")
            state["warning_anchor_at"] = now
        else:
            if now - state["signal_seen_at"] >= completion_timeout_seconds:
                timer_outcome["kind"] = "grace"
                return


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


async def _head_sha(sandbox: SandboxProvider, cwd: str) -> str:
    """Resolve `HEAD`'s SHA. Returns "" outside a git repo or on empty history."""
    result = await sandbox.exec(["git", "rev-parse", "HEAD"], cwd=cwd)
    if result.exit_code != 0:
        return ""
    return result.stdout.strip()


async def _commits_since(
    sandbox: SandboxProvider, cwd: str, pre_run_head: str
) -> list[str]:
    """Return SHAs created between the pre-run `HEAD` and the post-run `HEAD`.

    Empty when nothing was committed, or when the directory wasn't a git repo
    at run start (so there's no pre-run anchor to diff against).
    """
    if not pre_run_head:
        return []
    result = await sandbox.exec(
        ["git", "rev-list", f"{pre_run_head}..HEAD"], cwd=cwd
    )
    if result.exit_code != 0:
        return []
    return [sha for sha in (line.strip() for line in result.stdout.splitlines()) if sha]


def _dispatch_event(disp: Display, event: StreamEvent) -> None:
    if isinstance(event, TextEvent):
        disp.text(event.text)
    elif isinstance(event, ToolCallEvent):
        disp.tool_call(event.name, event.args)
    elif isinstance(event, SessionIdEvent):
        # Not surfaced to the display in this slice.
        pass


def _summary_rows(
    branch: str,
    usage: Usage | None,
    completion_signal: str | None,
    commits: list[str],
) -> dict[str, str]:
    rows: dict[str, str] = {"Branch": branch or "(unknown)"}
    rows["Completion signal"] = completion_signal or "(none — max iterations)"
    rows["Commits"] = (
        ", ".join(sha[:7] for sha in commits) if commits else "(none)"
    )
    if usage is None:
        rows["Token usage"] = "unavailable"
    else:
        rows["Input tokens"] = str(usage.input_tokens)
        rows["Output tokens"] = str(usage.output_tokens)
        rows["Cache read tokens"] = str(usage.cache_read_input_tokens)
        rows["Cache creation tokens"] = str(usage.cache_creation_input_tokens)
    return rows
