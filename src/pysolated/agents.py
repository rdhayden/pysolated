"""Agent providers — command building and stream parsing.

v1 ships one provider, `claude_code`. The stream parser and usage parser are
pure module-level functions (the provider delegates to them) so they can be
table-tested directly without constructing a provider.
"""

from __future__ import annotations

import json
from dataclasses import dataclass, field
from typing import Literal

from .core import (
    AgentCommandOptions,
    Command,
    SessionIdEvent,
    StreamEvent,
    TextEvent,
    ToolCallEvent,
    Usage,
)

# Allowlisted tools, mapped to the input field carrying the display arg.
# Anything not listed here is dropped — we never surface arbitrary tool input.
TOOL_ARG_FIELDS: dict[str, str] = {
    "Bash": "command",
    "WebSearch": "query",
    "WebFetch": "url",
    "Agent": "description",
}


def parse_stream_line(line: str) -> list[StreamEvent]:
    """Decode one Claude `stream-json` JSONL line into zero or more events.

    Pure. Non-JSON / unknown / malformed lines yield no events.

    - `assistant` lines yield `text` events (content text blocks concatenated)
      and `tool_call` events for allowlisted tools, in source order.
    - `system`/`init` lines yield a single `session_id` event.
    """
    if not line.startswith("{"):
        return []
    try:
        obj = json.loads(line)
    except (json.JSONDecodeError, ValueError):
        return []
    if not isinstance(obj, dict):
        return []

    message = obj.get("message")
    if obj.get("type") == "assistant" and isinstance(message, dict) and isinstance(
        message.get("content"), list
    ):
        events: list[StreamEvent] = []
        texts: list[str] = []
        for block in message["content"]:
            if not isinstance(block, dict):
                continue
            block_type = block.get("type")
            if block_type == "text" and isinstance(block.get("text"), str):
                texts.append(block["text"])
            elif (
                block_type == "tool_use"
                and isinstance(block.get("name"), str)
                and isinstance(block.get("input"), dict)
            ):
                arg_field = TOOL_ARG_FIELDS.get(block["name"])
                if arg_field is None:
                    continue  # not allowlisted
                arg_value = block["input"].get(arg_field)
                if not isinstance(arg_value, str):
                    continue  # missing / wrong-typed arg field
                if texts:
                    events.append(TextEvent(text="".join(texts)))
                    texts = []
                events.append(ToolCallEvent(name=block["name"], args=arg_value))
        if texts:
            events.append(TextEvent(text="".join(texts)))
        return events

    if (
        obj.get("type") == "system"
        and obj.get("subtype") == "init"
        and isinstance(obj.get("session_id"), str)
    ):
        return [SessionIdEvent(session_id=obj["session_id"])]

    return []


_USAGE_FIELDS = (
    "input_tokens",
    "cache_creation_input_tokens",
    "cache_read_input_tokens",
    "output_tokens",
)


def parse_session_usage(content: str) -> Usage | None:
    """Extract the session's authoritative token usage from streamed content.

    Pure. Scans the accumulated stream-json content from the end and returns the
    first complete usage block it finds, or `None` when no usage was emitted.

    The authoritative totals live on the terminal `result` line: an `assistant`
    line's usage is the `message_start` snapshot, captured before the response
    streamed, so its `output_tokens` is only a partial count (often 1). Scanning
    from the end reaches the `result` line first, so its totals win; an
    `assistant` line is the fallback for truncated streams that never reached a
    `result`. The usage block sits at the top level on a `result` line and under
    `message` on an `assistant` line.
    """
    for line in reversed(content.split("\n")):
        if not line.startswith("{"):
            continue
        try:
            obj = json.loads(line)
        except (json.JSONDecodeError, ValueError):
            continue
        if not isinstance(obj, dict):
            continue
        if obj.get("type") == "result":
            usage = obj.get("usage")
        elif obj.get("type") == "assistant":
            message = obj.get("message")
            usage = message.get("usage") if isinstance(message, dict) else None
        else:
            continue
        if not isinstance(usage, dict):
            continue
        if all(isinstance(usage.get(name), int) for name in _USAGE_FIELDS):
            return Usage(
                input_tokens=usage["input_tokens"],
                cache_creation_input_tokens=usage["cache_creation_input_tokens"],
                cache_read_input_tokens=usage["cache_read_input_tokens"],
                output_tokens=usage["output_tokens"],
            )
    return None


# Maps directly to Claude's `--permission-mode` flag. Mutually exclusive with
# `--dangerously-skip-permissions` on Claude's CLI.
PermissionMode = Literal[
    "default", "acceptEdits", "plan", "auto", "dontAsk", "bypassPermissions"
]


@dataclass(frozen=True)
class ClaudeCode:
    """The Claude Code agent provider.

    Build it via `claude_code(...)` rather than constructing directly.
    """

    model: str
    permission_mode: PermissionMode | None = None
    env: dict[str, str] = field(default_factory=dict)
    name: str = "claude-code"

    def build_command(self, options: AgentCommandOptions) -> Command:
        """Build the print-mode argv, with the prompt delivered on stdin.

        `permission_mode` and `--dangerously-skip-permissions` are mutually
        exclusive: an explicit mode replaces the default skip-permissions flag.
        """
        argv = ["claude", "--print", "--verbose"]
        if self.permission_mode is not None:
            argv += ["--permission-mode", self.permission_mode]
        else:
            argv.append("--dangerously-skip-permissions")
        argv += ["--output-format", "stream-json", "--model", self.model, "-p", "-"]
        return Command(argv=argv, stdin=options.prompt)

    def parse_stream_line(self, line: str) -> list[StreamEvent]:
        return parse_stream_line(line)

    def parse_session_usage(self, content: str) -> Usage | None:
        return parse_session_usage(content)


def claude_code(
    model: str,
    *,
    permission_mode: PermissionMode | None = None,
    env: dict[str, str] | None = None,
) -> ClaudeCode:
    """Create a Claude Code agent provider.

    `model` selects the Claude model. `permission_mode`, when given, replaces the
    default `--dangerously-skip-permissions` flag (the two are mutually exclusive).
    """
    return ClaudeCode(model=model, permission_mode=permission_mode, env=env or {})
