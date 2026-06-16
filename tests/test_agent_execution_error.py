"""Tests for `AgentExecutionError` — exit code + stderr/stdout tail surfacing.

The orchestrator raises this when the agent subprocess exits non-zero. Callers
read `exit_code`, `stderr_tail`, and `stdout_tail` off the exception; the
class is responsible for truncating long outputs to a *tail* so a developer
sees the crash explanation without scrolling past megabytes of stream-json.
"""

from __future__ import annotations

from pysolated.errors import AgentExecutionError, PysolatedError


def test_truncates_long_stderr_to_a_tail() -> None:
    """A stderr blob with hundreds of lines is reduced to a manageable tail.

    Crash explanations live at the end (last log line, last stack frame). A
    raw blob of thousands of lines drowns the signal — the error promises a
    *tail*, not the full stream.
    """
    long_stderr = "\n".join(f"noise line {i}" for i in range(500)) + "\nFINAL CRASH MESSAGE"

    err = AgentExecutionError(exit_code=1, stderr=long_stderr, stdout_tail="")

    # The last line — the actual crash explanation — must survive.
    assert "FINAL CRASH MESSAGE" in err.stderr_tail
    # The very first noise line must not — proves truncation actually happened.
    assert "noise line 0" not in err.stderr_tail
    # And the truncated tail must be small enough to read at a glance.
    assert err.stderr_tail.count("\n") <= 60


def test_truncates_long_stdout_to_a_tail() -> None:
    """stdout (the agent's full stream-json transcript) is also tailed."""
    long_stdout = (
        "\n".join(f'{{"type":"text","text":"chatter {i}"}}' for i in range(500))
        + '\n{"type":"text","text":"final assistant words"}'
    )

    err = AgentExecutionError(exit_code=2, stderr="", stdout_tail=long_stdout)

    assert "final assistant words" in err.stdout_tail
    assert "chatter 0" not in err.stdout_tail
    assert err.stdout_tail.count("\n") <= 60


def test_exposes_exit_code() -> None:
    err = AgentExecutionError(exit_code=127, stderr="command not found", stdout_tail="")
    assert err.exit_code == 127


def test_short_outputs_are_preserved_verbatim() -> None:
    """A short stderr is kept as-is — only oversized outputs are trimmed."""
    err = AgentExecutionError(
        exit_code=1, stderr="boom: missing token", stdout_tail="partial output"
    )
    assert err.stderr_tail == "boom: missing token"
    assert err.stdout_tail == "partial output"


def test_message_includes_exit_code_and_tail_content() -> None:
    """`str(err)` is what the CLI echoes to the user — must explain the crash.

    Without the tail in the message the user only sees `agent exited with
    code N` and has to dig into the exception attributes to find out *why*.
    """
    err = AgentExecutionError(
        exit_code=42, stderr="ENOENT: no such file 'config.yaml'", stdout_tail=""
    )
    text = str(err)
    assert "42" in text
    assert "ENOENT" in text
    assert "config.yaml" in text


def test_falls_back_to_stdout_tail_when_stderr_empty() -> None:
    """Some agents log to stdout, not stderr. The message must still explain why."""
    err = AgentExecutionError(
        exit_code=1, stderr="", stdout_tail="Error: invalid API key"
    )
    assert "invalid API key" in str(err)


def test_extends_pysolated_error_hierarchy() -> None:
    """Callers can blanket-catch `PysolatedError` to handle every library failure."""
    err = AgentExecutionError(exit_code=1, stderr="x", stdout_tail="")
    assert isinstance(err, PysolatedError)
