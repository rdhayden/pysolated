"""End-to-end test: a real failing subprocess surfaces a diagnosable error.

These exercise `run()` with the real `no_sandbox` provider so a host
subprocess actually runs and exits non-zero. The fake agent under test
emits a real argv (`sh -c "echo ... >&2; exit N"`) that crashes the way a
broken real agent would. Asserts the error type, the exit code, and that
the stderr tail explains the crash.
"""

from __future__ import annotations

import pytest

from pysolated import (
    AgentCommandOptions,
    Command,
    Severity,
    no_sandbox,
    parse_session_usage,
    parse_stream_line,
    run,
)
from pysolated.errors import AgentExecutionError


class CrashingAgent:
    """A fake agent whose command crashes on a real host subprocess."""

    name = "crashing-agent"
    env: dict[str, str] = {}

    def __init__(self, *, stderr_message: str, exit_code: int) -> None:
        self._stderr_message = stderr_message
        self._exit_code = exit_code

    def build_command(self, options: AgentCommandOptions) -> Command:
        # `sh -c` is a real subprocess. printf for portability — no trailing
        # interpretation surprises across platforms.
        script = f"printf %s {self._stderr_message!r} >&2; exit {self._exit_code}"
        return Command(argv=["sh", "-c", script])

    def parse_stream_line(self, line: str):
        return parse_stream_line(line)

    def parse_session_usage(self, content: str):
        return parse_session_usage(content)


class NullDisplay:
    """A display that records nothing — keeps the test output clean."""

    def intro(self, title: str) -> None: ...
    def status(self, message: str, severity: Severity) -> None: ...
    def text(self, message: str) -> None: ...
    def tool_call(self, name: str, formatted_args: str) -> None: ...
    def summary(self, title: str, rows: dict[str, str]) -> None: ...


async def test_real_subprocess_failure_raises_structured_error(tmp_path) -> None:
    """A real host subprocess exiting non-zero produces `AgentExecutionError`.

    Drives the full `run()` engine with the real `no_sandbox` provider so we
    cover the subprocess wiring, stderr capture, and error propagation end
    to end — not just the fake-sandbox path.
    """
    agent = CrashingAgent(stderr_message="AUTH_FAIL: invalid token", exit_code=9)

    with pytest.raises(AgentExecutionError) as exc:
        await run(
            agent=agent,
            sandbox=no_sandbox(),
            prompt="anything",
            cwd=str(tmp_path),
            display=NullDisplay(),
        )

    assert exc.value.exit_code == 9
    assert "AUTH_FAIL" in exc.value.stderr_tail
    assert "invalid token" in exc.value.stderr_tail
    # The message — what the CLI echoes — must include the crash hint too.
    assert "AUTH_FAIL" in str(exc.value)


async def test_real_subprocess_failure_message_distinct_from_idle_timeout(
    tmp_path,
) -> None:
    """Process-crash failures must not look like the idle-timeout failure.

    Both extend `PysolatedError`, but a caller catching the more specific
    `AgentExecutionError` should not accidentally swallow `IdleTimeoutError`
    and vice versa — they describe different failure modes.
    """
    from pysolated.errors import IdleTimeoutError

    agent = CrashingAgent(stderr_message="bad config", exit_code=1)
    with pytest.raises(AgentExecutionError) as exc:
        await run(
            agent=agent,
            sandbox=no_sandbox(),
            prompt="anything",
            cwd=str(tmp_path),
            display=NullDisplay(),
        )
    assert not isinstance(exc.value, IdleTimeoutError)
