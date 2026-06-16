"""Table tests for the pure completion-signal matcher."""

from __future__ import annotations

import pytest

from pysolated import match_completion_signal

CASES: list[tuple[str, str, str | list[str], str | None]] = [
    (
        "single string substring match",
        "doing things... <promise>COMPLETE</promise> trailing usage",
        "<promise>COMPLETE</promise>",
        "<promise>COMPLETE</promise>",
    ),
    (
        "single string no match",
        "nothing terminal here",
        "<promise>COMPLETE</promise>",
        None,
    ),
    (
        "list — first candidate wins by list order, not by position in content",
        "alpha then DONE then beta then ALL-DONE",
        ["ALL-DONE", "DONE"],
        "ALL-DONE",
    ),
    (
        "list — falls through to the second candidate when first is absent",
        "alpha then DONE then beta",
        ["ALL-DONE", "DONE"],
        "DONE",
    ),
    (
        "list — no candidate present yields None",
        "neither marker is in this content",
        ["ALL-DONE", "DONE"],
        None,
    ),
    (
        "empty content with non-empty signal yields None",
        "",
        "STOP",
        None,
    ),
    (
        "non-empty content with empty signal yields None (never auto-matches)",
        "anything",
        "",
        None,
    ),
    (
        "empty candidates in list are skipped, real one still matches",
        "STOP here",
        ["", "STOP"],
        "STOP",
    ),
    (
        "substring match, not whole-line — signal across surrounding text",
        "prefix-MARK-suffix",
        "MARK",
        "MARK",
    ),
    (
        "substring is case-sensitive",
        "mark in lowercase",
        "MARK",
        None,
    ),
    (
        "tuple input works the same as list",
        "tuple input STOP here",
        ("NOPE", "STOP"),
        "STOP",
    ),
]


@pytest.mark.parametrize(
    "desc,content,signals,expected", CASES, ids=[c[0] for c in CASES]
)
def test_match_completion_signal(
    desc: str, content: str, signals, expected
) -> None:
    assert match_completion_signal(content, signals) == expected
