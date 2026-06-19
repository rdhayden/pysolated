"""The Codex agent provider.

The stream parser and usage parser are pure module-level functions (the
provider delegates to them) so they can be table-tested directly without
constructing a provider — same pattern as `claude_code`.

Codex breaks two assumptions the original `StreamEvent` union was built on:

- Its final message arrives as one ``item.completed`` (``agent_message``)
  rather than streamed deltas; it still maps to ``TextEvent`` so prose holds
  it. The only visible difference is the end-of-turn single flush instead of
  live trickle (tool-call events still stream).
- Auth / rate-limit / API errors arrive in-band on stdout as
  ``{"type": "error", ...}`` lines (the process may even exit 0). These map
  to ``ResultEvent`` (ADR 0006) — a narrow channel used only for the
  stderr-empty fallback in ``AgentExecutionError`` plus a live error status.
"""

from __future__ import annotations

import json
from dataclasses import dataclass, field
from typing import Literal

from ..core import (
    AgentCommandOptions,
    Command,
    ResultEvent,
    SessionIdEvent,
    StreamEvent,
    TextEvent,
    ToolCallEvent,
    Usage,
)


def parse_codex_stream_line(line: str) -> list[StreamEvent]:
    """Decode one Codex ``--json`` JSONL line into zero or more events.

    Pure. Non-JSON / unknown / malformed lines yield no events.

    - ``thread.started`` → ``SessionIdEvent`` (unsurfaced this slice, same as
      Claude's; deferred to the agent-sessions slice).
    - ``item.completed`` with an ``agent_message`` item → ``TextEvent`` (the
      one-shot final message; feeds prose).
    - ``item.started`` with a ``command_execution`` item → ``ToolCallEvent``
      with name ``"Bash"`` (Codex's only allowlisted tool here — its
      command-execution channel is shell-shaped).
    - ``{"type": "error", ...}`` → ``ResultEvent`` (the in-band error channel,
      ADR 0006).
    """
    if not line.startswith("{"):
        return []
    try:
        obj = json.loads(line)
    except (json.JSONDecodeError, ValueError):
        return []
    if not isinstance(obj, dict):
        return []

    obj_type = obj.get("type")

    if obj_type == "thread.started" and isinstance(obj.get("thread_id"), str):
        return [SessionIdEvent(session_id=obj["thread_id"])]

    if obj_type == "item.completed":
        item = obj.get("item")
        if (
            isinstance(item, dict)
            and item.get("type") == "agent_message"
            and isinstance(item.get("text"), str)
        ):
            return [TextEvent(text=item["text"])]
        return []

    if obj_type == "item.started":
        item = obj.get("item")
        if (
            isinstance(item, dict)
            and item.get("type") == "command_execution"
            and isinstance(item.get("command"), str)
        ):
            return [ToolCallEvent(name="Bash", args=item["command"])]
        return []

    if obj_type == "error":
        message = obj.get("message")
        if isinstance(message, str):
            return [ResultEvent(text=message)]
        return []

    return []


_CODEX_USAGE_FIELDS = ("input_tokens", "cached_input_tokens", "output_tokens")


def parse_codex_session_usage(content: str) -> Usage | None:
    """Extract Codex's authoritative token usage from streamed content.

    Pure. Scans the accumulated content from the end and returns the first
    complete usage block on a ``turn.completed`` line, mapped to the
    four-field ``Usage``:

    - ``input_tokens = input_tokens - cached_input_tokens`` (Codex's
      ``input_tokens`` is the gross count; ``cached_input_tokens`` is a
      subset, subtracting avoids double-counting against the cache field).
    - ``cache_read_input_tokens = cached_input_tokens``.
    - ``cache_creation_input_tokens = 0`` (Codex does not report it).
    - ``output_tokens = output_tokens``.

    Missing or malformed → ``None``, same contract as Claude.
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
        if obj.get("type") != "turn.completed":
            continue
        usage = obj.get("usage")
        if not isinstance(usage, dict):
            continue
        if all(isinstance(usage.get(name), int) for name in _CODEX_USAGE_FIELDS):
            cached = usage["cached_input_tokens"]
            return Usage(
                input_tokens=usage["input_tokens"] - cached,
                cache_creation_input_tokens=0,
                cache_read_input_tokens=cached,
                output_tokens=usage["output_tokens"],
            )
    return None


CodexEffort = Literal["low", "medium", "high", "xhigh"]


@dataclass(frozen=True)
class Codex:
    """The Codex agent provider.

    Build it via ``codex(...)`` rather than constructing directly.

    The bypass flag (``--dangerously-bypass-approvals-and-sandbox``) is always
    emitted, with no opt-out — symmetric with ``claude_code``'s default
    ``--dangerously-skip-permissions``. ``effort``, when given, becomes a
    ``-c model_reasoning_effort="<effort>"`` override token; the literal
    quotes are required because Codex's ``-c`` parses TOML and no shell
    performs quote stripping.
    """

    model: str
    effort: CodexEffort | None = None
    env: dict[str, str] = field(default_factory=dict)
    name: str = "codex"

    def build_command(self, options: AgentCommandOptions) -> Command:
        argv: list[str] = [
            "codex",
            "exec",
            "--json",
            "--dangerously-bypass-approvals-and-sandbox",
            "-m",
            self.model,
        ]
        if self.effort is not None:
            argv += ["-c", f'model_reasoning_effort="{self.effort}"']
        return Command(argv=argv, stdin=options.prompt)

    def parse_stream_line(self, line: str) -> list[StreamEvent]:
        return parse_codex_stream_line(line)

    def parse_session_usage(self, content: str) -> Usage | None:
        return parse_codex_session_usage(content)


def codex(
    model: str,
    *,
    effort: CodexEffort | None = None,
    env: dict[str, str] | None = None,
) -> Codex:
    """Create a Codex agent provider.

    ``model`` is required (Codex has no default). ``effort`` (``low|medium|
    high|xhigh``) tunes Codex's reasoning effort and becomes a
    ``model_reasoning_effort`` TOML override on the Codex CLI.
    """
    return Codex(model=model, effort=effort, env=env or {})
