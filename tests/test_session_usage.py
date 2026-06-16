"""Tests for the pure session-usage parser."""

from __future__ import annotations

import json

from pysolated import Usage, parse_session_usage


def _assistant_with_usage(usage: dict, text: str = "hi") -> str:
    return json.dumps(
        {
            "type": "assistant",
            "message": {"content": [{"type": "text", "text": text}], "usage": usage},
        }
    )


FULL_USAGE = {
    "input_tokens": 9,
    "cache_creation_input_tokens": 7174,
    "cache_read_input_tokens": 17506,
    "output_tokens": 43,
}


def test_extracts_usage_from_single_assistant_line() -> None:
    content = _assistant_with_usage(FULL_USAGE)
    assert parse_session_usage(content) == Usage(**FULL_USAGE)


def test_last_assistant_usage_wins() -> None:
    first = _assistant_with_usage({**FULL_USAGE, "output_tokens": 1})
    last = _assistant_with_usage({**FULL_USAGE, "output_tokens": 99})
    content = "\n".join([first, last])
    result = parse_session_usage(content)
    assert result is not None
    assert result.output_tokens == 99


def test_ignores_trailing_non_assistant_lines() -> None:
    content = "\n".join(
        [
            _assistant_with_usage(FULL_USAGE),
            json.dumps({"type": "result", "subtype": "success", "result": "done"}),
        ]
    )
    assert parse_session_usage(content) == Usage(**FULL_USAGE)


def test_returns_none_when_no_usage_present() -> None:
    content = "\n".join(
        [
            json.dumps({"type": "system", "subtype": "init", "session_id": "x"}),
            json.dumps({"type": "assistant", "message": {"content": []}}),
        ]
    )
    assert parse_session_usage(content) is None


def test_returns_none_on_incomplete_usage() -> None:
    content = _assistant_with_usage({"input_tokens": 9, "output_tokens": 43})
    assert parse_session_usage(content) is None


def test_returns_none_on_empty_content() -> None:
    assert parse_session_usage("") is None
