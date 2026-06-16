"""Exception hierarchy for pysolated.

Per ADR 0002, failures raise exceptions rather than flowing through a typed
error channel. This hierarchy is deliberately small in v1; later slices extend
it (prompt-expansion errors, idle-timeout errors, structured-output errors).
"""

from __future__ import annotations


class PysolatedError(Exception):
    """Base class for every error raised by pysolated."""


class AgentExecutionError(PysolatedError):
    """The agent subprocess exited with a non-zero status.

    Carries the exit code plus a tail of stderr/stdout so the caller can
    diagnose a crashed agent.
    """

    def __init__(self, exit_code: int, stderr: str = "", stdout_tail: str = "") -> None:
        self.exit_code = exit_code
        self.stderr = stderr
        self.stdout_tail = stdout_tail
        detail = stderr.strip() or stdout_tail.strip() or "(no output captured)"
        super().__init__(f"agent exited with code {exit_code}: {detail}")


class IdleTimeoutError(PysolatedError):
    """The agent produced no output for longer than the configured idle window.

    Raised before any completion signal has been seen — once a signal appears,
    the completion-grace window takes over and succeeds-with-warning on expiry.
    """

    def __init__(self, timeout_seconds: float, stdout_tail: str = "") -> None:
        self.timeout_seconds = timeout_seconds
        self.stdout_tail = stdout_tail
        super().__init__(
            f"agent produced no output for {timeout_seconds:g}s (idle timeout)"
        )
