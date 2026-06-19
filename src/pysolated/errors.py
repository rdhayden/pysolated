"""Exception hierarchy for pysolated.

Per ADR 0002, failures raise exceptions rather than flowing through a typed
error channel. This hierarchy is deliberately small in v1; later slices extend
it (prompt-expansion errors, idle-timeout errors, structured-output errors).
"""

from __future__ import annotations

# Tail size for crash-diagnosis fields. Crash explanations live at the end of
# the stream (last log line, last stack frame), so the last N lines are kept
# and the rest is discarded. Picked to comfortably fit a stack trace or a few
# lines of agent prose while staying small enough to read in one terminal.
_MAX_TAIL_LINES = 50


def _tail(text: str, max_lines: int = _MAX_TAIL_LINES) -> str:
    """Return the last `max_lines` lines of `text`, preserving line order.

    Returns the input unchanged when it already fits. The line count is what
    matters here — crash output is line-oriented, and a per-byte cap would
    cut stack frames mid-word.
    """
    if not text:
        return text
    lines = text.splitlines()
    if len(lines) <= max_lines:
        return text
    return "\n".join(lines[-max_lines:])


class PysolatedError(Exception):
    """Base class for every error raised by pysolated."""


class AgentExecutionError(PysolatedError):
    """The agent subprocess exited with a non-zero status.

    Carries the exit code plus the *tail* of stderr/stdout — the part most
    likely to explain the crash. Long outputs are truncated to a fixed line
    count so a developer sees the error message instead of scrolling through
    megabytes of stream-json transcript.
    """

    def __init__(self, exit_code: int, stderr: str = "", stdout_tail: str = "") -> None:
        self.exit_code = exit_code
        self.stderr_tail = _tail(stderr)
        self.stdout_tail = _tail(stdout_tail)
        detail = (
            self.stderr_tail.strip()
            or self.stdout_tail.strip()
            or "(no output captured)"
        )
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


class MergeConflictError(PysolatedError):
    """A ``merge-to-head`` run's merge-back conflicted; nothing was discarded.

    The scratch branch and its worktree are **preserved** on disk so the user
    can finish the merge by hand. The error message lists the recovery
    commands: cd into the worktree, complete the merge, then delete the
    scratch branch. The merge in the user's main tree was aborted, so no
    conflict markers were left behind in their working copy.
    """

    def __init__(
        self,
        *,
        worktree_path: str,
        temp_branch: str,
        target_branch: str,
    ) -> None:
        self.worktree_path = worktree_path
        self.temp_branch = temp_branch
        self.target_branch = target_branch
        recovery = (
            f"  cd {worktree_path}\n"
            f"  git merge {temp_branch}\n"
            f"  git branch -D {temp_branch}"
        )
        super().__init__(
            f"merge of {temp_branch!r} into {target_branch!r} conflicted; "
            f"worktree preserved at {worktree_path}.\n"
            f"Resolve by hand:\n{recovery}"
        )
