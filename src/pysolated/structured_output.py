"""Structured output — pull a schema-validated payload out of the agent's stdout.

The caller asks the agent (in the prompt) to emit a payload inside a named XML
tag, e.g. `<result>{"answer": 42}</result>`. After the run, pysolated scans
stdout for that tag and either:

- **object mode** (`Output.object(tag, model)`) — JSON-parses the inner text and
  validates it against a Pydantic model, returning the validated instance.
- **string mode** (`Output.string(tag)`) — returns the inner text verbatim,
  whitespace-trimmed, with no parsing.

Orthogonal to the completion signal: a run may use either, both, or neither.

The module is pure / seam-free — `extract_structured_output` takes a string and
a definition and returns the validated payload (or raises). The orchestrator
wires it up after the single iteration completes; the two up-front guards
(``max_iterations == 1`` and the prompt containing the opening tag) live in
``run()`` itself so a misconfigured run fails before any agent work is paid for.

Behavior parity with the Sandcastle TypeScript original (ADR 0010):

- **Last match wins** when the tag appears multiple times in stdout. Agents
  self-correct (draft, then revise); end-first scanning picks the revision.
- **Fence-aware** object mode strips an optional ` ```json … ``` ` or bare
  ` ``` … ``` ` wrapper before parsing, since agents commonly wrap JSON that
  way without being asked.
- **Throw on failure**: missing tag, invalid JSON, or schema validation failure
  all raise `StructuredOutputError` carrying the raw matched text (when any)
  and the underlying cause for diagnosis.
"""

from __future__ import annotations

import json
import re
from dataclasses import dataclass
from typing import Any, Union

from pydantic import BaseModel, ValidationError

from .errors import PysolatedError


# ---------------------------------------------------------------------------
# Output definitions
# ---------------------------------------------------------------------------


@dataclass(frozen=True)
class OutputObject:
    """An object-typed structured output: JSON inside `<tag>...</tag>` validated
    against a Pydantic `model`. Build via `Output.object(tag, model)`.
    """

    tag: str
    model: type[BaseModel]


@dataclass(frozen=True)
class OutputString:
    """A string-typed structured output: the raw inner text of `<tag>...</tag>`,
    trimmed. Build via `Output.string(tag)`.
    """

    tag: str


OutputDefinition = Union[OutputObject, OutputString]
"""Union of every output shape `run()` accepts via its `output=` argument."""


class _OutputNamespace:
    """The `Output` namespace — sugar over the definition dataclasses.

    A class rather than a module so `Output.object(...)` / `Output.string(...)`
    behave like the TypeScript namespace they parallel without exposing two
    free functions at the package root.
    """

    @staticmethod
    def object(tag: str, model: type[BaseModel]) -> OutputObject:
        """Build an `OutputObject` for the given tag and Pydantic model."""
        return OutputObject(tag=tag, model=model)

    @staticmethod
    def string(tag: str) -> OutputString:
        """Build an `OutputString` for the given tag."""
        return OutputString(tag=tag)


Output = _OutputNamespace()
"""Public namespace: `Output.object(tag, model)` / `Output.string(tag)`."""


# ---------------------------------------------------------------------------
# Error
# ---------------------------------------------------------------------------


class StructuredOutputError(PysolatedError):
    """Raised when structured-output extraction or validation fails.

    Failure modes:

    - **Missing tag** — `<tag>...</tag>` was not found in stdout. `raw_matched`
      is `None`.
    - **Invalid JSON** (object mode) — the inner text did not parse as JSON.
      `cause` carries the underlying `json.JSONDecodeError`.
    - **Schema validation failure** (object mode) — the parsed JSON did not
      match the Pydantic model. `cause` carries the underlying
      `pydantic.ValidationError`.

    `tag` and `raw_matched` are exposed for diagnostics so the caller can
    correct the prompt or schema without re-running the agent. The original
    failure is preserved on `__cause__` (and `cause`) so library users can
    re-raise / inspect / log the validation issues directly.
    """

    def __init__(
        self,
        message: str,
        *,
        tag: str,
        raw_matched: str | None,
        cause: Exception | None = None,
    ) -> None:
        super().__init__(message)
        self.tag = tag
        self.raw_matched = raw_matched
        self.cause = cause
        if cause is not None:
            self.__cause__ = cause


# ---------------------------------------------------------------------------
# Extraction
# ---------------------------------------------------------------------------


# `<tag>...</tag>` — only the **last** match is used (see module docstring).
# A small helper rather than a regex so we can locate the last occurrence
# without DOTALL/greedy/backtracking surprises on large stdout streams.


def _find_last_tag_content(text: str, tag: str) -> str | None:
    """Return the text between the LAST `<tag>` and matching `</tag>`, or `None`.

    Walks left-to-right keeping only the most recent successful match. Mirrors
    `findLastTagContent` in the Sandcastle reference; preferred over a single
    `re.findall` so an open `<tag>` without a closing partner doesn't poison
    the result and an unbalanced closing tag is harmless.
    """
    open_tag = f"<{tag}>"
    close_tag = f"</{tag}>"
    last_content: str | None = None
    search_from = 0
    while True:
        open_idx = text.find(open_tag, search_from)
        if open_idx == -1:
            break
        content_start = open_idx + len(open_tag)
        close_idx = text.find(close_tag, content_start)
        if close_idx == -1:
            break
        last_content = text[content_start:close_idx]
        search_from = close_idx + len(close_tag)
    return last_content


_FENCE_RE = re.compile(
    r"\A```(?:json)?\s*\n([\s\S]*?)\n\s*```\s*\Z",
    re.MULTILINE,
)


def _unwrap_fences(text: str) -> str:
    """Strip an optional ` ```json … ``` ` (or bare ` ``` … ``` `) wrapper.

    Agents commonly wrap JSON in a Markdown code fence even when the prompt
    asks them not to. Unwrapping here keeps the parser tolerant in the one
    obvious way while still failing loudly on anything genuinely malformed.
    Returns the input unchanged when no fence is detected.
    """
    match = _FENCE_RE.match(text)
    if match is None:
        return text
    return match.group(1).strip()


def extract_structured_output(
    stdout: str, definition: OutputDefinition
) -> Any:
    """Extract the payload described by `definition` from `stdout`.

    - `OutputObject`: locates the last `<tag>...</tag>`, trims, unwraps a
      fence if present, `json.loads`, then validates against the Pydantic
      `model`. Returns the validated model instance.
    - `OutputString`: locates the last `<tag>...</tag>` and returns the
      inner text whitespace-trimmed.

    Raises `StructuredOutputError` on missing tag, JSON parse failure, or
    schema validation failure. Pure: no I/O, no side effects.
    """
    raw = _find_last_tag_content(stdout, definition.tag)
    if raw is None:
        raise StructuredOutputError(
            f"structured output tag <{definition.tag}> not found in agent output",
            tag=definition.tag,
            raw_matched=None,
        )

    if isinstance(definition, OutputString):
        return raw.strip()

    trimmed = raw.strip()
    unwrapped = _unwrap_fences(trimmed)
    try:
        parsed = json.loads(unwrapped)
    except json.JSONDecodeError as exc:
        raise StructuredOutputError(
            f"structured output tag <{definition.tag}> contains invalid JSON: {exc}",
            tag=definition.tag,
            raw_matched=raw,
            cause=exc,
        ) from exc

    try:
        return definition.model.model_validate(parsed)
    except ValidationError as exc:
        raise StructuredOutputError(
            f"structured output tag <{definition.tag}> failed schema validation: {exc}",
            tag=definition.tag,
            raw_matched=raw,
            cause=exc,
        ) from exc
