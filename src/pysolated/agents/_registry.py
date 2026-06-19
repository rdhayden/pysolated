"""Agent registry + CLI-builder.

A name → factory map keyed on each provider's ``.name``, plus ``build_agent``
— the CLI's one resolver. Library callers construct providers directly via
their typed factories and never touch this module; it exists only for the
string-name boundary the CLI (and later init/config) sits behind.

``build_agent`` applies provider-specific option handling here so the CLI
grows no ``if name == …`` ladder as agents are added. Argument errors raise
``ValueError``; the CLI translates that into ``typer.Exit(2)``, consistent
with the existing ``--prompt`` / ``--prompt-arg`` rejections.
"""

from __future__ import annotations

from typing import Callable

from ..core import AgentProvider
from .claude_code import PermissionMode, claude_code

_CLAUDE_CODE_DEFAULT_MODEL = "claude-opus-4-7"


def _build_claude_code(
    *,
    model: str | None,
    effort: str | None,
    permission_mode: str | None,
) -> AgentProvider:
    if effort is not None:
        raise ValueError("--effort is not supported by the claude-code agent.")
    resolved_model = model if model is not None else _CLAUDE_CODE_DEFAULT_MODEL
    return claude_code(
        resolved_model,
        permission_mode=permission_mode,  # type: ignore[arg-type]
    )


# Keyed on the provider's ``.name``. Each entry resolves CLI options into a
# concrete provider; provider-specific rejections live here, not in the CLI.
_REGISTRY: dict[
    str,
    Callable[..., AgentProvider],
] = {
    "claude-code": _build_claude_code,
}


def agent_names() -> list[str]:
    """The registered agent names, in insertion order — for error messages."""
    return list(_REGISTRY)


def build_agent(
    name: str,
    *,
    model: str | None,
    effort: str | None = None,
    permission_mode: PermissionMode | None = None,
) -> AgentProvider:
    """Resolve a CLI agent name to a configured ``AgentProvider``.

    Raises ``ValueError`` for an unknown name, a model that's required but
    missing for the chosen agent, or a flag the chosen agent does not accept.
    """
    factory = _REGISTRY.get(name)
    if factory is None:
        valid = ", ".join(agent_names())
        raise ValueError(f"Unknown --agent {name!r}. Valid agents: {valid}.")
    return factory(model=model, effort=effort, permission_mode=permission_mode)
