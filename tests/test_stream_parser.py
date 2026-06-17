"""Table tests for the pure stream parser."""

from __future__ import annotations

import json

import pytest

from pysolated import (
    SessionIdEvent,
    StreamEvent,
    TextEvent,
    ToolCallEvent,
    parse_stream_line,
)


def _assistant(content: list[dict]) -> str:
    return json.dumps({"type": "assistant", "message": {"content": content}})


CASES: list[tuple[str, str, list[StreamEvent]]] = [
    (
        "single text block",
        _assistant([{"type": "text", "text": "Hi!"}]),
        [TextEvent("Hi!")],
    ),
    (
        "multiple text blocks concatenated",
        _assistant(
            [{"type": "text", "text": "Hello "}, {"type": "text", "text": "world"}]
        ),
        [TextEvent("Hello world")],
    ),
    (
        "allowlisted Bash tool_use",
        _assistant(
            [{"type": "tool_use", "name": "Bash", "input": {"command": "ls -la"}}]
        ),
        [ToolCallEvent("Bash", "ls -la")],
    ),
    (
        "allowlisted WebSearch tool_use",
        _assistant(
            [{"type": "tool_use", "name": "WebSearch", "input": {"query": "python"}}]
        ),
        [ToolCallEvent("WebSearch", "python")],
    ),
    (
        "allowlisted WebFetch tool_use",
        _assistant(
            [{"type": "tool_use", "name": "WebFetch", "input": {"url": "http://x"}}]
        ),
        [ToolCallEvent("WebFetch", "http://x")],
    ),
    (
        "allowlisted Agent tool_use",
        _assistant(
            [{"type": "tool_use", "name": "Agent", "input": {"description": "go"}}]
        ),
        [ToolCallEvent("Agent", "go")],
    ),
    (
        "text then tool flushes text first, in order",
        _assistant(
            [
                {"type": "text", "text": "running:"},
                {"type": "tool_use", "name": "Bash", "input": {"command": "pwd"}},
            ]
        ),
        [TextEvent("running:"), ToolCallEvent("Bash", "pwd")],
    ),
    (
        "non-allowlisted tool dropped",
        _assistant(
            [{"type": "tool_use", "name": "Read", "input": {"file_path": "/x"}}]
        ),
        [],
    ),
    (
        "tool with missing arg field dropped",
        _assistant([{"type": "tool_use", "name": "Bash", "input": {"timeout": 5}}]),
        [],
    ),
    (
        "thinking block ignored, text kept",
        _assistant(
            [
                {"type": "thinking", "thinking": "hmm"},
                {"type": "text", "text": "answer"},
            ]
        ),
        [TextEvent("answer")],
    ),
    (
        "system/init yields session_id",
        json.dumps({"type": "system", "subtype": "init", "session_id": "abc-123"}),
        [SessionIdEvent("abc-123")],
    ),
    (
        "system without init subtype yields nothing",
        json.dumps({"type": "system", "subtype": "thinking_tokens", "session_id": "x"}),
        [],
    ),
    ("non-JSON line yields nothing", "not json at all", []),
    ("empty line yields nothing", "", []),
    ("malformed JSON yields nothing", '{"type": "assistant"', []),
    (
        "result line yields nothing in this slice",
        json.dumps({"type": "result", "subtype": "success", "result": "done"}),
        [],
    ),
    (
        "assistant with non-list content yields nothing",
        json.dumps({"type": "assistant", "message": {"content": "oops"}}),
        [],
    ),
]


@pytest.mark.parametrize("desc,line,expected", CASES, ids=[c[0] for c in CASES])
def test_parse_stream_line(desc: str, line: str, expected: list[StreamEvent]) -> None:
    assert parse_stream_line(line) == expected


def test_text_and_two_tools_interleaved() -> None:
    line = _assistant(
        [
            {"type": "text", "text": "a"},
            {"type": "tool_use", "name": "Bash", "input": {"command": "one"}},
            {"type": "text", "text": "b"},
            {"type": "tool_use", "name": "Bash", "input": {"command": "two"}},
        ]
    )
    assert parse_stream_line(line) == [
        TextEvent("a"),
        ToolCallEvent("Bash", "one"),
        TextEvent("b"),
        ToolCallEvent("Bash", "two"),
    ]
