"""Prompt pipeline — turn a prompt source into the text the agent receives.

Two paths, deliberately asymmetric:

- **Inline prompt** — passed verbatim. No `{{KEY}}` substitution, no `` !`cmd` ``
  expansion. Supplying `prompt_args` alongside an inline prompt is rejected up
  front because they would be silently ignored.
- **Prompt template** (file source) — runs two stages in order:

  1. **Argument substitution** — `{{KEY}}` placeholders are replaced from
     `user_args` overlaid on `built_in_args`. A user argument that shadows a
     built-in is rejected so the caller never silently overrides framework
     values; a placeholder with no matching argument is rejected so a typo
     never reaches the agent as the literal `{{KEY}}` token.
  2. **Prompt expansion** — `` !`command` `` markers are replaced by the
     stdout of the command, evaluated via an injected executor (the **sandbox
     seam** in production, a fake in tests). A non-zero exit fails the run
     immediately so a broken command never yields a silently-truncated prompt.

The substitution and expansion helpers are pure / seam-driven so they can be
table-tested directly. `resolve_prompt` is the public entry point used by the
orchestrator: it picks the path, applies the guards, reads the file, and runs
the two stages.
"""

from __future__ import annotations

import re
from collections.abc import Awaitable, Callable
from pathlib import Path

from .core import ExecResult
from .errors import PysolatedError

# Identifier-style placeholder names: letters, digits, underscores; no leading
# digit. Restrictive on purpose — keeps `{{ ...complex... }}` out of scope.
_PLACEHOLDER_RE = re.compile(r"\{\{\s*([A-Za-z_][A-Za-z0-9_]*)\s*\}\}")

# `!`command`` — backticks bound the command and may not contain a backtick.
# A leading literal `!` distinguishes shell expressions from any other backtick
# usage that might appear in prose.
_SHELL_EXPR_RE = re.compile(r"!`([^`]*)`")


PromptExecutor = Callable[[str], Awaitable[ExecResult]]
"""The seam used to run shell expressions during prompt expansion.

Takes a single command string (what was inside the `` !`...` `` backticks)
and returns an `ExecResult`. Concretely the orchestrator wires this through
the sandbox provider; tests pass a fake.
"""


class PromptError(PysolatedError):
    """Base class for prompt-pipeline failures."""


class PromptArgumentError(PromptError):
    """The caller's prompt-pipeline arguments are invalid.

    Covers: passing args with an inline prompt, a user arg that would shadow
    a built-in, a placeholder with no matching argument, and ambiguous source
    selection (both `prompt` and `prompt_file`, or neither).
    """


class PromptExpansionError(PromptError):
    """A `` !`command` `` shell expression exited non-zero during expansion."""

    def __init__(
        self,
        command: str,
        exit_code: int,
        stderr: str = "",
        stdout: str = "",
    ) -> None:
        self.command = command
        self.exit_code = exit_code
        self.stderr = stderr
        self.stdout = stdout
        detail = stderr.strip() or stdout.strip() or "(no output captured)"
        super().__init__(
            f"prompt shell expression {command!r} failed (exit {exit_code}): {detail}"
        )


def substitute_arguments(
    template: str,
    *,
    user_args: dict[str, str],
    built_in_args: dict[str, str],
) -> str:
    """Replace `{{KEY}}` placeholders in `template` with merged argument values.

    `built_in_args` form the base; `user_args` overlay them — but a user key
    that collides with a built-in raises `PromptArgumentError` first, so in
    practice the merged map only contains disjoint keys. A placeholder whose
    key is missing from the merged map also raises `PromptArgumentError`.
    Pure: no I/O.
    """
    overlap = sorted(set(user_args) & set(built_in_args))
    if overlap:
        names = ", ".join(overlap)
        raise PromptArgumentError(
            f"prompt_args may not override built-in argument(s): {names}"
        )
    merged: dict[str, str] = {**built_in_args, **user_args}

    missing: list[str] = []

    def _replace(match: re.Match[str]) -> str:
        key = match.group(1)
        if key not in merged:
            missing.append(key)
            return match.group(0)  # placeholder is restored below in the error
        return merged[key]

    result = _PLACEHOLDER_RE.sub(_replace, template)
    if missing:
        unique = sorted(dict.fromkeys(missing))
        names = ", ".join(unique)
        raise PromptArgumentError(
            f"prompt template references unknown argument(s): {names}"
        )
    return result


async def expand_shell_expressions(
    template: str, *, executor: PromptExecutor
) -> str:
    """Replace each `` !`command` `` in `template` with the command's stdout.

    `executor` runs the command and returns an `ExecResult`. A non-zero exit
    raises `PromptExpansionError` immediately — no partial prompt is returned.
    The replacement is the command's stdout with a single trailing newline
    stripped (so a one-line command stays on one line in the prompt).
    Seam-driven (the executor is injected) but otherwise pure.
    """
    # Sequential expansion, in source order. Concurrency would break ordering
    # of side-effects in commands that read shared state.
    pieces: list[str] = []
    cursor = 0
    for match in _SHELL_EXPR_RE.finditer(template):
        pieces.append(template[cursor:match.start()])
        command = match.group(1)
        result = await executor(command)
        if result.exit_code != 0:
            raise PromptExpansionError(
                command=command,
                exit_code=result.exit_code,
                stderr=result.stderr,
                stdout=result.stdout,
            )
        # Drop a single trailing newline so `` !`echo hi` `` interpolates
        # cleanly inline. Multi-line stdout is preserved as-is otherwise.
        stdout = result.stdout
        if stdout.endswith("\n"):
            stdout = stdout[:-1]
        pieces.append(stdout)
        cursor = match.end()
    pieces.append(template[cursor:])
    return "".join(pieces)


async def resolve_prompt(
    *,
    inline: str | None,
    file: str | Path | None,
    user_args: dict[str, str] | None,
    built_in_args: dict[str, str],
    executor: PromptExecutor,
) -> str:
    """Resolve a prompt source to the final text the agent will receive.

    Exactly one of `inline` and `file` must be supplied:

    - Inline path: returns `inline` verbatim. `user_args` must be empty —
      passing them with an inline prompt is a `PromptArgumentError` so the
      caller learns immediately that they would be ignored. A literal
      `` !`...` `` in an inline prompt is never executed.
    - Template path: reads `file`, runs `substitute_arguments` (with
      `user_args` overlaid on `built_in_args` after the overlap guard), then
      `expand_shell_expressions` via the injected `executor`.
    """
    if inline is not None and file is not None:
        raise PromptArgumentError(
            "pass either an inline prompt or a prompt_file, not both"
        )
    if inline is None and file is None:
        raise PromptArgumentError(
            "no prompt source supplied: pass either an inline prompt or a prompt_file"
        )
    args = user_args or {}

    if inline is not None:
        if args:
            raise PromptArgumentError(
                "prompt_args are not allowed with an inline prompt — "
                "inline prompts skip substitution, so the args would be ignored"
            )
        return inline

    assert file is not None  # mypy/readability
    template = Path(file).read_text(encoding="utf-8")
    substituted = substitute_arguments(
        template, user_args=args, built_in_args=built_in_args
    )
    return await expand_shell_expressions(substituted, executor=executor)
