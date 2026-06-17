"""Core value types and the three injectable Protocol seams.

Everything here is shared by the provider impls, the display, and the
orchestrator. Keeping the seams (`AgentProvider`, `SandboxProvider`, `Display`)
and the values they exchange in one module avoids import cycles: the concrete
impls and the engine all depend on this module and nothing depends back on them.

Per ADR 0002 the seams are plain `typing.Protocol`s injected as values; per ADR
0001 the agent/sandbox boundary is an argv list plus optional stdin, never a
shell string.
"""

from __future__ import annotations

from dataclasses import dataclass
from typing import Callable, Literal, Protocol, runtime_checkable

from pydantic import BaseModel, ConfigDict, Field

# ---------------------------------------------------------------------------
# Stream events — the decoded output of one agent stream-json line.
# ---------------------------------------------------------------------------


@dataclass(frozen=True)
class TextEvent:
    """Assistant prose emitted by the agent."""

    text: str


@dataclass(frozen=True)
class ToolCallEvent:
    """An allowlisted tool invocation, already reduced to its display arg."""

    name: str
    args: str


@dataclass(frozen=True)
class SessionIdEvent:
    """The agent session id, surfaced once at stream start (system/init)."""

    session_id: str


StreamEvent = TextEvent | ToolCallEvent | SessionIdEvent


# ---------------------------------------------------------------------------
# Token usage.
# ---------------------------------------------------------------------------


class Usage(BaseModel):
    """Token usage extracted from an agent session.

    Frozen so a `RunResult` cannot be mutated after the run completes.
    """

    model_config = ConfigDict(frozen=True)

    input_tokens: int
    cache_creation_input_tokens: int
    cache_read_input_tokens: int
    output_tokens: int


# ---------------------------------------------------------------------------
# Agent/sandbox command boundary (ADR 0001).
# ---------------------------------------------------------------------------


@dataclass(frozen=True)
class Command:
    """An agent invocation: an argv list plus optional stdin content.

    Not a shell string — the sandbox spawns argv directly, so there is no
    shell, no quoting, and no `shlex` escaping footgun.
    """

    argv: list[str]
    stdin: str | None = None


@dataclass(frozen=True)
class AgentCommandOptions:
    """Inputs an agent provider needs to build its command for one iteration."""

    prompt: str


@dataclass(frozen=True)
class ExecResult:
    """Outcome of running a command through a sandbox."""

    exit_code: int
    stdout: str
    stderr: str


# ---------------------------------------------------------------------------
# Display.
# ---------------------------------------------------------------------------

Severity = Literal["info", "success", "warn", "error"]


@runtime_checkable
class Display(Protocol):
    """The presentation seam: where the orchestrator narrates a run.

    Also the orchestrator's test-substitution point — a fake `Display` records
    the calls and lets tests assert on observable output without a terminal.
    """

    def intro(self, title: str) -> None: ...

    def status(self, message: str, severity: Severity) -> None: ...

    def text(self, message: str) -> None: ...

    def tool_call(self, name: str, formatted_args: str) -> None: ...

    def summary(self, title: str, rows: dict[str, str]) -> None: ...


# ---------------------------------------------------------------------------
# Agent provider.
# ---------------------------------------------------------------------------


@runtime_checkable
class AgentProvider(Protocol):
    """Builds commands and parses output for a specific agent (e.g. Claude Code)."""

    @property
    def name(self) -> str: ...

    @property
    def env(self) -> dict[str, str]: ...

    def build_command(self, options: AgentCommandOptions) -> Command: ...

    def parse_stream_line(self, line: str) -> list[StreamEvent]: ...

    def parse_session_usage(self, content: str) -> Usage | None: ...


# ---------------------------------------------------------------------------
# Sandbox provider.
# ---------------------------------------------------------------------------


@runtime_checkable
class Sandbox(Protocol):
    """A live sandbox environment, returned by `SandboxProvider.create()`.

    Owns the running environment for one `run()` — the host subprocess on
    `no_sandbox`, a long-lived container on a future container provider. Created
    once and `close()`d in a `finally` that covers every exit path (success,
    exception, idle-timeout, abort, Ctrl-C). `close()` MUST be idempotent: the
    orchestrator calls it in `finally`, and a per-handle `atexit` backstop may
    call it again on abnormal exit.

    `exec` MUST stream stdout line-by-line via `on_line` as it arrives — that is
    how live feedback (and idle timeouts) work. A buffered impl that only calls
    `on_line` after the process exits does not satisfy the contract.
    """

    async def exec(
        self,
        argv: list[str],
        *,
        stdin: str | None = None,
        cwd: str | None = None,
        on_line: Callable[[str], None] | None = None,
    ) -> ExecResult: ...

    async def close(self) -> None: ...


@runtime_checkable
class SandboxProvider(Protocol):
    """Factory for sandboxes — frozen configuration with one method: `create()`.

    Splitting the seam into a factory and a live handle (ADR 0003) lets
    long-lived providers own a single environment across a run (`podman run -d`
    once, many `podman exec`, `podman rm -f` at `close()`) while keeping
    providers frozen and concurrency-safe — each `create()` yields an
    independent sandbox, so one configured provider can drive concurrent runs
    without state corruption.
    """

    @property
    def name(self) -> str: ...

    @property
    def env(self) -> dict[str, str]: ...

    async def create(self, work_dir: str) -> Sandbox: ...


# ---------------------------------------------------------------------------
# Run result.
# ---------------------------------------------------------------------------


class RunResult(BaseModel):
    """The frozen result of a `run()` — what the agent said and where it ran.

    `output` carries the structured-output payload extracted after the run when
    the caller passed `output=Output.object(...)` / `Output.string(...)` to
    `run()`; `None` for runs without an `output` argument.

    `log_file_path` is the resolved path of the log file when the run used the
    file display (`log_file=` on `run()`); `None` otherwise.
    """

    model_config = ConfigDict(frozen=True, arbitrary_types_allowed=True)

    iterations: int
    stdout: str
    branch: str
    usage: Usage | None = None
    completion_signal: str | None = None
    commits: list[str] = Field(default_factory=list)
    output: str | BaseModel | None = None
    log_file_path: str | None = None
