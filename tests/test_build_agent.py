"""Tests for the `build_agent` CLI-builder + agent registry (issue #32).

These verify the name → provider seam at the registry boundary: provider
resolution, the `claude-opus-4-7` default-model rule that applies only to
`claude-code`, foreign-option rejection, and the unknown-agent error.

Library callers don't use `build_agent` — they import the typed factories
directly. The registry is the string-name boundary the CLI sits behind.
"""

from __future__ import annotations

import pytest

from pysolated.agents import ClaudeCode
from pysolated.agents._registry import build_agent


def test_build_agent_resolves_claude_code_with_explicit_model() -> None:
    agent = build_agent("claude-code", model="claude-haiku-4-5-20251001")
    assert isinstance(agent, ClaudeCode)
    assert agent.model == "claude-haiku-4-5-20251001"
    assert agent.permission_mode is None


def test_build_agent_claude_code_default_model_applies_when_model_is_none() -> None:
    agent = build_agent("claude-code", model=None)
    assert isinstance(agent, ClaudeCode)
    assert agent.model == "claude-opus-4-7"


def test_build_agent_claude_code_passes_permission_mode() -> None:
    agent = build_agent("claude-code", model=None, permission_mode="auto")
    assert isinstance(agent, ClaudeCode)
    assert agent.permission_mode == "auto"


def test_build_agent_rejects_effort_on_claude_code() -> None:
    with pytest.raises(ValueError) as info:
        build_agent("claude-code", model=None, effort="high")
    assert "--effort" in str(info.value)
    assert "claude-code" in str(info.value)


def test_build_agent_unknown_name_errors_and_lists_valid_agents() -> None:
    with pytest.raises(ValueError) as info:
        build_agent("codex", model="gpt-5")
    msg = str(info.value)
    assert "codex" in msg
    assert "claude-code" in msg
