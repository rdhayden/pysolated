"""pysolated — orchestrate AI coding agents inside sandboxes via `run()`."""

from __future__ import annotations

from .agents import (
    ClaudeCode,
    Codex,
    CodexEffort,
    PermissionMode,
    claude_code,
    codex,
    parse_codex_session_usage,
    parse_codex_stream_line,
    parse_session_usage,
    parse_stream_line,
)
from .completion import match_completion_signal
from .core import (
    AgentCommandOptions,
    AgentProvider,
    Command,
    Display,
    ExecResult,
    ResultEvent,
    RunResult,
    Sandbox,
    SandboxProvider,
    SessionIdEvent,
    Severity,
    StreamEvent,
    TextEvent,
    ToolCallEvent,
    Usage,
)
from .display import FileDisplay, TerminalDisplay
from .errors import (
    AgentExecutionError,
    BranchAlreadyCheckedOutError,
    IdleTimeoutError,
    MergeConflictError,
    PysolatedError,
)
from .orchestrator import (
    DEFAULT_COMPLETION_SIGNAL,
    DEFAULT_COMPLETION_TIMEOUT_SECONDS,
    DEFAULT_IDLE_TIMEOUT_SECONDS,
    DEFAULT_IDLE_WARNING_INTERVAL_SECONDS,
    run,
)
from .prompts import (
    PromptArgumentError,
    PromptError,
    PromptExecutor,
    PromptExpansionError,
    expand_shell_expressions,
    resolve_prompt,
    substitute_arguments,
)
from .sandboxes import (
    Docker,
    DockerHandle,
    DockerImageNotFoundError,
    DockerImageUidMismatchError,
    DockerLaunchError,
    Mount,
    NoSandbox,
    NoSandboxHandle,
    Podman,
    PodmanHandle,
    PodmanImageNotFoundError,
    PodmanLaunchError,
    docker,
    no_sandbox,
    podman,
)
from .structured_output import (
    Output,
    OutputDefinition,
    OutputObject,
    OutputString,
    StructuredOutputError,
    extract_structured_output,
)
from .worktrees import (
    BranchStrategy,
    FinalizedRun,
    HeadStrategy,
    MergeToHeadStrategy,
    NamedBranchStrategy,
    PreparedRun,
)

__all__ = [
    # Entry point
    "run",
    # Providers
    "claude_code",
    "ClaudeCode",
    "codex",
    "Codex",
    "CodexEffort",
    "no_sandbox",
    "NoSandbox",
    "NoSandboxHandle",
    "podman",
    "Podman",
    "PodmanHandle",
    "docker",
    "Docker",
    "DockerHandle",
    "Mount",
    "PermissionMode",
    # Display
    "TerminalDisplay",
    "FileDisplay",
    # Seams (Protocols)
    "AgentProvider",
    "SandboxProvider",
    "Sandbox",
    "Display",
    # Pure parsers / matchers
    "parse_stream_line",
    "parse_session_usage",
    "parse_codex_stream_line",
    "parse_codex_session_usage",
    "match_completion_signal",
    # Prompt pipeline
    "resolve_prompt",
    "substitute_arguments",
    "expand_shell_expressions",
    "PromptExecutor",
    # Structured output
    "Output",
    "OutputDefinition",
    "OutputObject",
    "OutputString",
    "extract_structured_output",
    # Defaults
    "DEFAULT_COMPLETION_SIGNAL",
    "DEFAULT_IDLE_TIMEOUT_SECONDS",
    "DEFAULT_COMPLETION_TIMEOUT_SECONDS",
    "DEFAULT_IDLE_WARNING_INTERVAL_SECONDS",
    # Branch strategies
    "BranchStrategy",
    "HeadStrategy",
    "MergeToHeadStrategy",
    "NamedBranchStrategy",
    "PreparedRun",
    "FinalizedRun",
    # Value types
    "RunResult",
    "Usage",
    "Command",
    "ExecResult",
    "AgentCommandOptions",
    "StreamEvent",
    "TextEvent",
    "ToolCallEvent",
    "SessionIdEvent",
    "ResultEvent",
    "Severity",
    # Errors
    "PysolatedError",
    "AgentExecutionError",
    "BranchAlreadyCheckedOutError",
    "IdleTimeoutError",
    "MergeConflictError",
    "PromptError",
    "PromptArgumentError",
    "PromptExpansionError",
    "StructuredOutputError",
    "PodmanImageNotFoundError",
    "PodmanLaunchError",
    "DockerImageNotFoundError",
    "DockerImageUidMismatchError",
    "DockerLaunchError",
]
