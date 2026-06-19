"""Table tests for the pure Codex stream parser.

Codex's JSONL stream uses a typed-envelope shape: ``thread.started`` opens the
turn, ``item.started`` / ``item.completed`` carry typed items, ``turn.completed``
ends it, and ``error`` is the in-band error channel (ADR 0006).
"""

from __future__ import annotations

import json

import pytest

from pysolated import (
    ResultEvent,
    SessionIdEvent,
    StreamEvent,
    TextEvent,
    ToolCallEvent,
    parse_codex_stream_line,
)

CASES: list[tuple[str, str, list[StreamEvent]]] = [
    (
        "thread.started yields session id",
        json.dumps({"type": "thread.started", "thread_id": "thread-abc"}),
        [SessionIdEvent("thread-abc")],
    ),
    (
        "item.completed agent_message yields text",
        json.dumps(
            {
                "type": "item.completed",
                "item": {"type": "agent_message", "text": "All done."},
            }
        ),
        [TextEvent("All done.")],
    ),
    (
        "item.started command_execution yields Bash tool call",
        json.dumps(
            {
                "type": "item.started",
                "item": {"type": "command_execution", "command": "ls -la"},
            }
        ),
        [ToolCallEvent("Bash", "ls -la")],
    ),
    (
        "error yields ResultEvent",
        json.dumps({"type": "error", "message": "Error: invalid API key"}),
        [ResultEvent("Error: invalid API key")],
    ),
    (
        "turn.completed yields nothing (usage parsed separately)",
        json.dumps(
            {
                "type": "turn.completed",
                "usage": {
                    "input_tokens": 100,
                    "cached_input_tokens": 50,
                    "output_tokens": 25,
                },
            }
        ),
        [],
    ),
    (
        "item.completed of unknown subtype yields nothing",
        json.dumps(
            {
                "type": "item.completed",
                "item": {"type": "file_change", "path": "/tmp/x"},
            }
        ),
        [],
    ),
    (
        "item.started of unknown subtype yields nothing",
        json.dumps(
            {
                "type": "item.started",
                "item": {"type": "reasoning", "text": "thinking..."},
            }
        ),
        [],
    ),
    (
        "agent_message with non-string text yields nothing",
        json.dumps(
            {
                "type": "item.completed",
                "item": {"type": "agent_message", "text": 42},
            }
        ),
        [],
    ),
    (
        "command_execution with non-string command yields nothing",
        json.dumps(
            {
                "type": "item.started",
                "item": {"type": "command_execution", "command": ["ls", "-la"]},
            }
        ),
        [],
    ),
    (
        "error with non-string message yields nothing",
        json.dumps({"type": "error", "message": {"code": 500}}),
        [],
    ),
    (
        "thread.started without thread_id yields nothing",
        json.dumps({"type": "thread.started"}),
        [],
    ),
    (
        "unknown top-level type yields nothing",
        json.dumps({"type": "session.started", "session_id": "x"}),
        [],
    ),
    ("non-JSON line yields nothing", "not json at all", []),
    ("empty line yields nothing", "", []),
    ("malformed JSON yields nothing", '{"type": "error"', []),
    (
        "non-object top level yields nothing",
        json.dumps(["not", "an", "object"]),
        [],
    ),
    (
        "item.completed without an item dict yields nothing",
        json.dumps({"type": "item.completed", "item": "oops"}),
        [],
    ),
]


@pytest.mark.parametrize("desc,line,expected", CASES, ids=[c[0] for c in CASES])
def test_parse_codex_stream_line(
    desc: str, line: str, expected: list[StreamEvent]
) -> None:
    assert parse_codex_stream_line(line) == expected
