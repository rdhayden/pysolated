"""Prompt-pipeline tests — table-driven, seam-injected, no real subprocess.

The module under test is `pysolated.prompts`. It splits into three units:

- `substitute_arguments` (pure) — {{KEY}} replacement, missing-key error,
  built-in shadowing error.
- `expand_shell_expressions` (seam-driven, pure otherwise) — !`cmd` is replaced
  by stdout via an injected executor; a non-zero exit raises immediately.
- `resolve_prompt` (orchestrator-facing) — picks inline vs file template,
  enforces the inline+args guard, threads built-ins under user args.

A small `make_executor` helper records the commands an executor was asked to
run so we can assert the inline path never invokes shell expansion.
"""

from __future__ import annotations

from collections.abc import Awaitable, Callable
from pathlib import Path

import pytest

from pysolated import ExecResult
from pysolated.prompts import (
    PromptArgumentError,
    PromptExpansionError,
    expand_shell_expressions,
    resolve_prompt,
    substitute_arguments,
)


def make_executor(
    script: dict[str, ExecResult] | None = None,
    *,
    default: ExecResult | None = None,
) -> tuple[Callable[[str], Awaitable[ExecResult]], list[str]]:
    """Build a deterministic fake executor plus a list of commands it received."""
    calls: list[str] = []
    table = script or {}

    async def execute(command: str) -> ExecResult:
        calls.append(command)
        if command in table:
            return table[command]
        if default is not None:
            return default
        # Default success: empty stdout so missing scripts don't surprise tests.
        return ExecResult(exit_code=0, stdout="", stderr="")

    return execute, calls


# ---------------------------------------------------------------------------
# substitute_arguments — pure
# ---------------------------------------------------------------------------


@pytest.mark.parametrize(
    "template,user_args,built_ins,expected",
    [
        # Plain substitution, single key.
        ("hello {{name}}", {"name": "world"}, {}, "hello world"),
        # Multiple keys, repeated keys, whitespace tolerated inside braces.
        (
            "{{a}}+{{ b }}+{{a}}",
            {"a": "1", "b": "two"},
            {},
            "1+two+1",
        ),
        # No placeholders → unchanged.
        ("plain text, no braces", {"unused": "x"}, {}, "plain text, no braces"),
        # User args overlay built-ins (disjoint keys).
        (
            "user={{user}}; branch={{branch}}",
            {"user": "robin"},
            {"branch": "main"},
            "user=robin; branch=main",
        ),
        # `{{KEY}}` left intact when it isn't an identifier (e.g. spaces inside).
        (
            "{{ has space }} stays",
            {},
            {},
            "{{ has space }} stays",
        ),
    ],
)
def test_substitute_arguments_table(template, user_args, built_ins, expected) -> None:
    assert (
        substitute_arguments(
            template, user_args=user_args, built_in_args=built_ins
        )
        == expected
    )


def test_substitute_arguments_missing_key_raises() -> None:
    with pytest.raises(PromptArgumentError) as exc:
        substitute_arguments(
            "Hi {{name}}, branch {{branch}}, run {{run_id}}",
            user_args={"name": "robin"},
            built_in_args={"branch": "main"},
        )
    # Names of the missing key(s) are surfaced in the error.
    assert "run_id" in str(exc.value)


def test_substitute_arguments_user_override_of_builtin_rejected() -> None:
    with pytest.raises(PromptArgumentError) as exc:
        substitute_arguments(
            "{{branch}}",
            user_args={"branch": "evil"},
            built_in_args={"branch": "main"},
        )
    assert "branch" in str(exc.value)


# ---------------------------------------------------------------------------
# expand_shell_expressions — seam-driven
# ---------------------------------------------------------------------------


async def test_expand_shell_expressions_replaces_with_stdout() -> None:
    executor, calls = make_executor(
        {
            "git log -1": ExecResult(exit_code=0, stdout="abc123 fix bug\n", stderr=""),
        }
    )
    result = await expand_shell_expressions(
        "Last commit: !`git log -1`", executor=executor
    )
    # Trailing newline is stripped so the substitution sits cleanly inline.
    assert result == "Last commit: abc123 fix bug"
    assert calls == ["git log -1"]


async def test_expand_shell_expressions_multiple_in_source_order() -> None:
    executor, calls = make_executor(
        {
            "echo a": ExecResult(exit_code=0, stdout="A\n", stderr=""),
            "echo b": ExecResult(exit_code=0, stdout="B\n", stderr=""),
        }
    )
    out = await expand_shell_expressions(
        "<!`echo a`|!`echo b`>", executor=executor
    )
    assert out == "<A|B>"
    assert calls == ["echo a", "echo b"]


async def test_expand_shell_expressions_no_markers_passes_through() -> None:
    executor, calls = make_executor()
    out = await expand_shell_expressions("nothing to expand", executor=executor)
    assert out == "nothing to expand"
    assert calls == []  # Executor never invoked when template has no markers.


async def test_expand_shell_expressions_nonzero_exit_raises() -> None:
    executor, _ = make_executor(
        {
            "fail-me": ExecResult(exit_code=2, stdout="", stderr="boom"),
        }
    )
    with pytest.raises(PromptExpansionError) as exc:
        await expand_shell_expressions(
            "before !`fail-me` after", executor=executor
        )
    assert exc.value.exit_code == 2
    assert exc.value.command == "fail-me"
    assert "boom" in str(exc.value)


async def test_expand_shell_expressions_stops_at_first_failure() -> None:
    # The first command fails; the second must NOT run — so no
    # partially-expanded prompt can leak through.
    executor, calls = make_executor(
        {
            "first": ExecResult(exit_code=1, stdout="", stderr="nope"),
            "second": ExecResult(exit_code=0, stdout="ok", stderr=""),
        }
    )
    with pytest.raises(PromptExpansionError):
        await expand_shell_expressions(
            "!`first` then !`second`", executor=executor
        )
    assert calls == ["first"]


async def test_expand_shell_expressions_preserves_internal_newlines() -> None:
    executor, _ = make_executor(
        {
            "log": ExecResult(exit_code=0, stdout="line1\nline2\n", stderr=""),
        }
    )
    out = await expand_shell_expressions(
        "log:\n!`log`\n---", executor=executor
    )
    # Only the final trailing newline is stripped; internal newlines stay.
    assert out == "log:\nline1\nline2\n---"


# ---------------------------------------------------------------------------
# resolve_prompt — inline vs file dispatch and the inline+args guard
# ---------------------------------------------------------------------------


async def test_resolve_prompt_inline_returns_verbatim() -> None:
    executor, calls = make_executor()
    out = await resolve_prompt(
        inline="  literal {{KEY}} !`do not run`  ",
        file=None,
        user_args=None,
        built_in_args={"branch": "main"},
        executor=executor,
    )
    # No substitution. No expansion. The executor is never called for an inline
    # prompt — that is the load-bearing inline guarantee.
    assert out == "  literal {{KEY}} !`do not run`  "
    assert calls == []


async def test_resolve_prompt_rejects_args_alongside_inline() -> None:
    executor, _ = make_executor()
    with pytest.raises(PromptArgumentError) as exc:
        await resolve_prompt(
            inline="hi",
            file=None,
            user_args={"who": "robin"},
            built_in_args={"branch": "main"},
            executor=executor,
        )
    assert "inline" in str(exc.value).lower()


async def test_resolve_prompt_template_runs_both_stages(
    tmp_path: Path,
) -> None:
    template = tmp_path / "prompt.txt"
    template.write_text(
        "Hello {{name}} on {{branch}}.\nRecent: !`git log -1`\n", encoding="utf-8"
    )
    executor, calls = make_executor(
        {"git log -1": ExecResult(exit_code=0, stdout="abc fix\n", stderr="")}
    )

    out = await resolve_prompt(
        inline=None,
        file=template,
        user_args={"name": "robin"},
        built_in_args={"branch": "feat/x"},
        executor=executor,
    )

    assert out == "Hello robin on feat/x.\nRecent: abc fix\n"
    assert calls == ["git log -1"]


async def test_resolve_prompt_template_rejects_user_override_of_builtin(
    tmp_path: Path,
) -> None:
    template = tmp_path / "p.txt"
    template.write_text("{{branch}}", encoding="utf-8")
    executor, _ = make_executor()
    with pytest.raises(PromptArgumentError) as exc:
        await resolve_prompt(
            inline=None,
            file=template,
            user_args={"branch": "evil"},  # collides with built-in
            built_in_args={"branch": "main"},
            executor=executor,
        )
    assert "branch" in str(exc.value)


async def test_resolve_prompt_template_propagates_shell_failure(
    tmp_path: Path,
) -> None:
    template = tmp_path / "p.txt"
    template.write_text("X = !`broken`\n", encoding="utf-8")
    executor, _ = make_executor(
        {"broken": ExecResult(exit_code=3, stdout="", stderr="kaboom")}
    )
    with pytest.raises(PromptExpansionError) as exc:
        await resolve_prompt(
            inline=None,
            file=template,
            user_args=None,
            built_in_args={"branch": "main"},
            executor=executor,
        )
    assert exc.value.exit_code == 3


async def test_resolve_prompt_requires_a_source() -> None:
    executor, _ = make_executor()
    with pytest.raises(PromptArgumentError):
        await resolve_prompt(
            inline=None,
            file=None,
            user_args=None,
            built_in_args={"branch": "main"},
            executor=executor,
        )


async def test_resolve_prompt_rejects_both_sources(tmp_path: Path) -> None:
    template = tmp_path / "p.txt"
    template.write_text("hi", encoding="utf-8")
    executor, _ = make_executor()
    with pytest.raises(PromptArgumentError):
        await resolve_prompt(
            inline="hi",
            file=template,
            user_args=None,
            built_in_args={"branch": "main"},
            executor=executor,
        )
