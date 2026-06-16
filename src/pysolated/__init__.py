"""pysolated — orchestrate AI coding agents inside sandboxes via `run()`."""

from __future__ import annotations

from .agents import (
    ClaudeCode,
    PermissionMode,
    claude_code,
    parse_session_usage,
    parse_stream_line,
)
from .core import (
    AgentCommandOptions,
    AgentProvider,
    Command,
    Display,
    ExecResult,
    RunResult,
    SandboxProvider,
    SessionIdEvent,
    Severity,
    StreamEvent,
    TextEvent,
    ToolCallEvent,
    Usage,
)
from .display import TerminalDisplay
from .errors import AgentExecutionError, PysolatedError
from .orchestrator import run
from .sandboxes import NoSandbox, no_sandbox

__all__ = [
    # Entry point
    "run",
    # Providers
    "claude_code",
    "ClaudeCode",
    "no_sandbox",
    "NoSandbox",
    "PermissionMode",
    # Display
    "TerminalDisplay",
    # Seams (Protocols)
    "AgentProvider",
    "SandboxProvider",
    "Display",
    # Pure parsers
    "parse_stream_line",
    "parse_session_usage",
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
    "Severity",
    # Errors
    "PysolatedError",
    "AgentExecutionError",
]
