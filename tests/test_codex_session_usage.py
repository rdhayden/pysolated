"""Tests for the pure Codex session-usage parser.

Codex emits `{type: "turn.completed", usage: {input_tokens, cached_input_tokens,
output_tokens}}`. pysolated maps that onto its four-field `Usage`:

- `input_tokens` = `input_tokens - cached_input_tokens` (cached is a subset of
  the gross input count; subtracting avoids double-counting).
- `cache_read_input_tokens` = `cached_input_tokens`.
- `cache_creation_input_tokens` = 0 (Codex does not report it).
- `output_tokens` = `output_tokens`.
"""

from __future__ import annotations

import json

from pysolated import Usage, parse_codex_session_usage


def _turn_completed(usage: dict) -> str:
    return json.dumps({"type": "turn.completed", "usage": usage})


def test_extracts_usage_with_cached_subtracted_from_input() -> None:
    content = _turn_completed(
        {"input_tokens": 100, "cached_input_tokens": 30, "output_tokens": 42}
    )
    result = parse_codex_session_usage(content)
    assert result == Usage(
        input_tokens=70,
        cache_creation_input_tokens=0,
        cache_read_input_tokens=30,
        output_tokens=42,
    )


def test_zero_cached_tokens_still_parses() -> None:
    content = _turn_completed(
        {"input_tokens": 100, "cached_input_tokens": 0, "output_tokens": 42}
    )
    result = parse_codex_session_usage(content)
    assert result == Usage(
        input_tokens=100,
        cache_creation_input_tokens=0,
        cache_read_input_tokens=0,
        output_tokens=42,
    )


def test_last_turn_completed_wins() -> None:
    first = _turn_completed(
        {"input_tokens": 10, "cached_input_tokens": 0, "output_tokens": 1}
    )
    last = _turn_completed(
        {"input_tokens": 200, "cached_input_tokens": 50, "output_tokens": 99}
    )
    content = "\n".join([first, last])
    result = parse_codex_session_usage(content)
    assert result is not None
    assert result.output_tokens == 99
    assert result.input_tokens == 150  # 200 - 50
    assert result.cache_read_input_tokens == 50


def test_returns_none_when_no_turn_completed_present() -> None:
    content = "\n".join(
        [
            json.dumps({"type": "thread.started", "thread_id": "x"}),
            json.dumps(
                {
                    "type": "item.completed",
                    "item": {"type": "agent_message", "text": "hi"},
                }
            ),
        ]
    )
    assert parse_codex_session_usage(content) is None


def test_returns_none_on_incomplete_usage() -> None:
    content = _turn_completed({"input_tokens": 9, "output_tokens": 43})
    assert parse_codex_session_usage(content) is None


def test_returns_none_on_empty_content() -> None:
    assert parse_codex_session_usage("") is None


def test_ignores_non_object_or_non_json_lines() -> None:
    content = "\n".join(
        [
            "not json at all",
            json.dumps(["not", "an", "object"]),
            _turn_completed(
                {"input_tokens": 50, "cached_input_tokens": 10, "output_tokens": 5}
            ),
        ]
    )
    result = parse_codex_session_usage(content)
    assert result == Usage(
        input_tokens=40,
        cache_creation_input_tokens=0,
        cache_read_input_tokens=10,
        output_tokens=5,
    )
