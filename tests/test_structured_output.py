"""Pure tests for `extract_structured_output` and the `Output` helpers.

The module under test is `pysolated.structured_output`. It splits into two
units:

- The `Output` namespace — `Output.object(tag, model)` / `Output.string(tag)`
  build frozen definitions consumed by the extractor and by `run()`.
- `extract_structured_output` (pure, no I/O) — locates the last
  `<tag>...</tag>` in stdout and returns the validated payload (object mode)
  or the trimmed inner text (string mode), raising `StructuredOutputError`
  on missing tag / invalid JSON / schema validation failure.

The tests intentionally exercise the externally observable contract: the
return value, the raised error, the error's surfaced fields. Internal helpers
(fence-unwrap, last-match scan) are exercised through the public extractor
rather than tested directly.
"""

from __future__ import annotations

import json

import pytest
from pydantic import BaseModel, Field, ValidationError

from pysolated import (
    Output,
    OutputObject,
    OutputString,
    StructuredOutputError,
    extract_structured_output,
)


class Answer(BaseModel):
    """A tiny Pydantic model used across the object-mode tests."""

    answer: int
    note: str = "ok"


class StrictAnswer(BaseModel):
    """Forbids unknown fields so we can test a clear validation failure path."""

    model_config = {"extra": "forbid"}

    answer: int


# ---------------------------------------------------------------------------
# `Output` namespace — definition builders
# ---------------------------------------------------------------------------


def test_output_object_builds_frozen_definition() -> None:
    """`Output.object(tag, model)` returns a frozen `OutputObject`.

    The result must be a plain `OutputObject` so `run()` can branch on it via
    `isinstance`, and it must be frozen so the orchestrator can store it
    safely without callers mutating its fields mid-run.
    """
    definition = Output.object("result", Answer)
    assert isinstance(definition, OutputObject)
    assert definition.tag == "result"
    assert definition.model is Answer
    with pytest.raises(Exception):
        definition.tag = "other"  # type: ignore[misc]


def test_output_string_builds_frozen_definition() -> None:
    """`Output.string(tag)` returns a frozen `OutputString` with the tag set."""
    definition = Output.string("summary")
    assert isinstance(definition, OutputString)
    assert definition.tag == "summary"
    with pytest.raises(Exception):
        definition.tag = "other"  # type: ignore[misc]


# ---------------------------------------------------------------------------
# Object mode — the table-tested success and failure paths
# ---------------------------------------------------------------------------


def test_object_mode_parses_and_validates_simple_payload() -> None:
    """The JSON inside the tag is parsed and validated into the Pydantic model.

    Covers the happy path: one tag, well-formed JSON, schema match. The result
    must be an instance of the caller's model so they get typed access (and
    Pydantic's downstream conveniences) on `RunResult.output`.
    """
    stdout = 'preamble\n<result>{"answer": 42, "note": "great"}</result>\ndone'
    value = extract_structured_output(stdout, Output.object("result", Answer))
    assert isinstance(value, Answer)
    assert value.answer == 42
    assert value.note == "great"


def test_object_mode_uses_last_occurrence_when_tag_repeats() -> None:
    """Agents often self-correct by re-emitting the tag; the LAST match wins.

    Matches Sandcastle ADR 0010: a single tag may appear multiple times when
    an agent revises its answer mid-stream. Picking the last occurrence
    follows what a human reader would assume is the final answer.
    """
    stdout = (
        '<result>{"answer": 1}</result>\n'
        "rethinking...\n"
        '<result>{"answer": 2}</result>'
    )
    value = extract_structured_output(stdout, Output.object("result", Answer))
    assert value.answer == 2


def test_object_mode_unwraps_json_fence_inside_tag() -> None:
    """A ` ```json … ``` ` wrapper inside the tag is stripped before parsing.

    Agents commonly wrap JSON in a Markdown fence even when not asked to. The
    extractor unwraps it so that benign formatting doesn't trip JSON parse;
    anything genuinely malformed still raises.
    """
    stdout = "<result>\n```json\n" + '{"answer": 7}' + "\n```\n</result>"
    value = extract_structured_output(stdout, Output.object("result", Answer))
    assert value.answer == 7


def test_object_mode_unwraps_bare_fence_without_language_hint() -> None:
    """A bare ` ``` … ``` ` wrapper (no `json` hint) is also stripped."""
    stdout = "<result>\n```\n" + '{"answer": 9}' + "\n```\n</result>"
    value = extract_structured_output(stdout, Output.object("result", Answer))
    assert value.answer == 9


def test_object_mode_tolerates_whitespace_around_json() -> None:
    """Whitespace between the tag boundaries and the JSON payload is ignored."""
    stdout = '<result>\n   {"answer": 3}   \n</result>'
    value = extract_structured_output(stdout, Output.object("result", Answer))
    assert value.answer == 3


def test_object_mode_missing_tag_raises_with_raw_matched_none() -> None:
    """A run that produced no `<tag>...</tag>` surfaces a clear error.

    `raw_matched` is `None` so the caller can distinguish "tag never appeared"
    from "tag appeared but content was bad" without parsing the message.
    """
    stdout = "the agent forgot to emit anything tagged"
    with pytest.raises(StructuredOutputError) as exc:
        extract_structured_output(stdout, Output.object("result", Answer))
    assert exc.value.tag == "result"
    assert exc.value.raw_matched is None
    assert "<result>" in str(exc.value)


def test_object_mode_invalid_json_raises_with_raw_matched_and_cause() -> None:
    """Invalid JSON in the tag raises with the raw text and the `JSONDecodeError`.

    The raw text helps a caller log/inspect what the agent actually wrote,
    and the cause makes the failure mode programmatically detectable.
    """
    stdout = "<result>this is not json</result>"
    with pytest.raises(StructuredOutputError) as exc:
        extract_structured_output(stdout, Output.object("result", Answer))
    assert exc.value.tag == "result"
    assert exc.value.raw_matched == "this is not json"
    assert isinstance(exc.value.cause, json.JSONDecodeError)


def test_object_mode_schema_validation_failure_raises_with_validation_error() -> None:
    """A payload that parses but doesn't match the schema raises with the
    Pydantic `ValidationError` preserved on `cause` for full diagnostics.
    """
    stdout = '<result>{"wrong_field": 1}</result>'
    with pytest.raises(StructuredOutputError) as exc:
        extract_structured_output(stdout, Output.object("result", StrictAnswer))
    assert exc.value.tag == "result"
    assert exc.value.raw_matched == '{"wrong_field": 1}'
    assert isinstance(exc.value.cause, ValidationError)


def test_object_mode_supports_nested_payload() -> None:
    """Nested objects and lists round-trip through extraction.

    Models with composite fields are the realistic shape for structured output;
    this guards against a parser that handles only flat objects.
    """

    class Item(BaseModel):
        name: str
        score: int

    class Report(BaseModel):
        items: list[Item]
        meta: dict[str, str] = Field(default_factory=dict)

    stdout = (
        '<r>{"items": [{"name": "a", "score": 1}, '
        '{"name": "b", "score": 2}], "meta": {"k": "v"}}</r>'
    )
    value = extract_structured_output(stdout, Output.object("r", Report))
    assert [(i.name, i.score) for i in value.items] == [("a", 1), ("b", 2)]
    assert value.meta == {"k": "v"}


# ---------------------------------------------------------------------------
# String mode
# ---------------------------------------------------------------------------


def test_string_mode_returns_trimmed_inner_text() -> None:
    """`Output.string` returns the inner text with leading/trailing whitespace
    stripped — agents tend to pad tag contents with newlines.
    """
    stdout = "preamble\n<summary>  the headline   </summary>\nepilogue"
    value = extract_structured_output(stdout, Output.string("summary"))
    assert value == "the headline"


def test_string_mode_uses_last_occurrence() -> None:
    """As with object mode, the last occurrence wins on string extraction."""
    stdout = "<note>first</note>\nstuff\n<note>final</note>"
    value = extract_structured_output(stdout, Output.string("note"))
    assert value == "final"


def test_string_mode_does_not_json_parse_or_validate() -> None:
    """The string mode returns the raw inner text — no JSON parsing.

    The same content that would be valid JSON in object mode comes back as
    its literal string here. This is the explicit contract that distinguishes
    the two modes: string mode never reaches the JSON layer.
    """
    stdout = '<data>{"this": "is just text"}</data>'
    value = extract_structured_output(stdout, Output.string("data"))
    assert value == '{"this": "is just text"}'


def test_string_mode_preserves_internal_newlines_after_trim() -> None:
    """Multi-line content inside the tag is preserved verbatim after the trim."""
    stdout = "<notes>\nline one\nline two\n</notes>"
    value = extract_structured_output(stdout, Output.string("notes"))
    assert value == "line one\nline two"


def test_string_mode_missing_tag_raises_structured_output_error() -> None:
    """Missing-tag handling matches object mode: `raw_matched=None` and a
    message that names the tag the caller asked for.
    """
    stdout = "no tag in sight"
    with pytest.raises(StructuredOutputError) as exc:
        extract_structured_output(stdout, Output.string("summary"))
    assert exc.value.tag == "summary"
    assert exc.value.raw_matched is None


# ---------------------------------------------------------------------------
# Edge cases shared by both modes
# ---------------------------------------------------------------------------


def test_unbalanced_open_tag_alone_is_treated_as_missing() -> None:
    """An open tag without a matching close tag is treated as missing.

    Partial output mid-stream is the realistic cause; the extractor refuses
    to guess and instead surfaces the same "tag not found" error so the
    caller can correct the prompt or re-run the agent.
    """
    stdout = "<result>{\"answer\": 1}"  # no closing </result>
    with pytest.raises(StructuredOutputError) as exc:
        extract_structured_output(stdout, Output.object("result", Answer))
    assert exc.value.raw_matched is None


def test_tag_name_is_matched_literally_not_as_a_prefix() -> None:
    """`<results>` does NOT satisfy a request for `<result>`.

    A literal substring scan would mistakenly accept `<results>...</results>`
    when asked for `<result>`. The extractor matches the exact open/close
    boundary so similarly-named tags don't pollute the result.
    """
    stdout = '<results>{"answer": 1}</results>'
    with pytest.raises(StructuredOutputError) as exc:
        extract_structured_output(stdout, Output.object("result", Answer))
    assert exc.value.raw_matched is None
