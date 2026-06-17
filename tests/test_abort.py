"""Tests for abort/cancellation — `signal=` aborts mid-run and kills the subprocess.

The orchestrator-level tests drive fake seams so cancellation is verified
deterministically; the subprocess-level test exercises `no_sandbox` against a
real `sleep` to prove the kill path reaches an OS process and the run returns
promptly.
"""

from __future__ import annotations

import asyncio
import os
import time
from typing import Callable

import pytest

from pysolated import (
    AgentCommandOptions,
    Command,
    ExecResult,
    Severity,
    no_sandbox,
    parse_session_usage,
    parse_stream_line,
    run,
)


class PassthroughAgent:
    """Agent stub whose command argv is supplied at construction time."""

    name = "passthrough"
    env: dict[str, str] = {}

    def __init__(self, argv: list[str] | None = None) -> None:
        self._argv = argv or ["scripted"]

    def build_command(self, options: AgentCommandOptions) -> Command:
        return Command(argv=self._argv, stdin=options.prompt)

    def parse_stream_line(self, line: str):
        return parse_stream_line(line)

    def parse_session_usage(self, content: str):
        return parse_session_usage(content)


class HangingSandbox:
    """Fake sandbox that answers git probes and then hangs in the agent exec.

    Sets `agent_exec_started` once the agent invocation enters its hang so
    tests can synchronise the abort precisely (no sleep races).
    """

    name = "hanging-sandbox"
    env: dict[str, str] = {}

    def __init__(self, branch: str = "main") -> None:
        self._branch = branch
        self.agent_exec_started = asyncio.Event()
        self.agent_exec_cancelled = False
        self.closed = False

    async def create(self, work_dir: str) -> "HangingSandbox":
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
        self.agent_exec_started.set()
        try:
            await asyncio.Event().wait()
        except asyncio.CancelledError:
            self.agent_exec_cancelled = True
            raise
        return ExecResult(exit_code=0, stdout="", stderr="")


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


class CountingSandbox:
    """Fake sandbox that completes each agent invocation immediately.

    Records how many times the agent argv was exec'd and fires
    `first_iteration_done` after the first finishes — gives between-iteration
    abort tests a precise hook to set the signal at exactly the right moment.
    """

    name = "counting-sandbox"
    env: dict[str, str] = {}

    def __init__(self) -> None:
        self.agent_calls = 0
        self.first_iteration_done = asyncio.Event()
        self._continue = asyncio.Event()
        self.closed = False

    def allow_next_iteration(self) -> None:
        self._continue.set()

    async def create(self, work_dir: str) -> "CountingSandbox":
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
            return ExecResult(exit_code=0, stdout="main\n", stderr="")
        if argv[:2] == ["git", "rev-parse"]:
            return ExecResult(exit_code=0, stdout="", stderr="")
        if argv[:2] == ["git", "rev-list"]:
            return ExecResult(exit_code=0, stdout="", stderr="")
        self.agent_calls += 1
        if self.agent_calls == 1:
            self.first_iteration_done.set()
            await self._continue.wait()
        return ExecResult(exit_code=0, stdout="", stderr="")


async def test_signal_aborts_between_iterations() -> None:
    """Firing the signal between iterations stops the outer loop before the next exec."""
    sandbox = CountingSandbox()
    abort = asyncio.Event()

    task = asyncio.create_task(
        run(
            agent=PassthroughAgent(),
            sandbox=sandbox,
            prompt="go",
            display=RecordingDisplay(),
            signal=abort,
            max_iterations=3,
            idle_timeout_seconds=10.0,
            completion_timeout_seconds=10.0,
            idle_warning_interval_seconds=10.0,
        )
    )

    await sandbox.first_iteration_done.wait()
    abort.set()
    sandbox.allow_next_iteration()

    with pytest.raises(asyncio.CancelledError):
        await asyncio.wait_for(task, timeout=2.0)
    assert sandbox.agent_calls == 1, (
        f"expected exactly 1 agent invocation; got {sandbox.agent_calls}"
    )


async def test_pre_fired_signal_aborts_before_agent_runs() -> None:
    """If the signal is already set when `run()` enters its loop, no iteration starts."""
    sandbox = HangingSandbox()
    abort = asyncio.Event()
    abort.set()

    with pytest.raises(asyncio.CancelledError):
        await run(
            agent=PassthroughAgent(),
            sandbox=sandbox,
            prompt="go",
            display=RecordingDisplay(),
            signal=abort,
            idle_timeout_seconds=10.0,
            completion_timeout_seconds=10.0,
            idle_warning_interval_seconds=10.0,
        )
    assert not sandbox.agent_exec_started.is_set(), (
        "agent should never have been invoked when the signal was pre-set"
    )


async def test_signal_kills_real_host_subprocess(tmp_path) -> None:
    """End-to-end kill path: abort propagates from `signal=` to a real OS PID.

    `no_sandbox` runs the agent argv as a real subprocess. The agent here is
    a long `sleep`; once the abort fires we expect the run to stop in well
    under the sleep duration and the PID to no longer exist.
    """
    # Seed a git repo so the orchestrator's pre/post-run git probes succeed.
    sandbox = no_sandbox()
    seeding_handle = await sandbox.create(work_dir=str(tmp_path))
    for argv in (
        ["git", "init", "-q"],
        ["git", "config", "user.email", "t@t"],
        ["git", "config", "user.name", "t"],
        ["git", "commit", "--allow-empty", "-q", "-m", "init"],
    ):
        result = await seeding_handle.exec(argv, cwd=str(tmp_path))
        assert result.exit_code == 0, result.stderr
    await seeding_handle.close()

    # The agent's "command" is a Python subprocess that prints a sentinel PID
    # line (so we know it's running) then sleeps long enough that finishing
    # naturally would dwarf the abort window.
    pid_file = tmp_path / "agent.pid"
    script = (
        "import os, sys, time; "
        f"open({str(pid_file)!r}, 'w').write(str(os.getpid())); "
        "sys.stdout.write('ready\\n'); sys.stdout.flush(); "
        "time.sleep(60)"
    )
    agent = PassthroughAgent(argv=["python3", "-c", script])

    abort = asyncio.Event()
    started = time.monotonic()
    task = asyncio.create_task(
        run(
            agent=agent,
            sandbox=sandbox,
            prompt="go",
            cwd=str(tmp_path),
            display=RecordingDisplay(),
            signal=abort,
            idle_timeout_seconds=30.0,
            completion_timeout_seconds=30.0,
            idle_warning_interval_seconds=30.0,
        )
    )

    # Wait until the subprocess has actually written its PID before aborting.
    for _ in range(200):
        if pid_file.exists():
            break
        await asyncio.sleep(0.02)
    assert pid_file.exists(), "child subprocess never started"
    child_pid = int(pid_file.read_text().strip())

    abort.set()
    with pytest.raises(asyncio.CancelledError):
        await asyncio.wait_for(task, timeout=5.0)
    elapsed = time.monotonic() - started
    assert elapsed < 5.0, f"abort did not stop the run promptly (took {elapsed:.2f}s)"

    # The child process should be gone. Give the kernel a brief moment to reap.
    for _ in range(50):
        try:
            os.kill(child_pid, 0)
        except ProcessLookupError:
            break
        await asyncio.sleep(0.02)
    else:
        pytest.fail(f"child PID {child_pid} survived abort")


async def test_signal_aborts_in_flight_iteration() -> None:
    """Setting the abort signal mid-iteration cancels the iteration promptly.

    The fake sandbox's agent invocation hangs forever; firing the signal must
    propagate cancellation into that hang (verified by the sandbox seeing
    `CancelledError`) and surface to the caller as `asyncio.CancelledError`.
    """
    sandbox = HangingSandbox()
    abort = asyncio.Event()

    task = asyncio.create_task(
        run(
            agent=PassthroughAgent(),
            sandbox=sandbox,
            prompt="go",
            display=RecordingDisplay(),
            signal=abort,
            idle_timeout_seconds=10.0,
            completion_timeout_seconds=10.0,
            idle_warning_interval_seconds=10.0,
        )
    )

    await sandbox.agent_exec_started.wait()
    abort.set()

    with pytest.raises(asyncio.CancelledError):
        await asyncio.wait_for(task, timeout=2.0)
    assert sandbox.agent_exec_cancelled, (
        "expected the in-flight sandbox exec to receive CancelledError"
    )
