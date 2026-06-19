"""Agent providers — command building and stream parsing.

v1 ships one provider, `claude_code`. The stream parser and usage parser are
pure module-level functions (the provider delegates to them) so they can be
table-tested directly without constructing a provider.

The package layout mirrors `sandboxes/` (see issue #24):

- `claude_code.py` — the Claude Code provider plus its pure parsers.
- `_parsing.py` — shared stream-parsing helpers (the tool-input allowlist and
  the assistant-content block parser) that Claude — and the later Copilot
  provider — both use.
- `_registry.py` — empty scaffold for the registry + `build_agent` to land in
  a follow-up slice (issue #32).
"""

from __future__ import annotations

from .claude_code import (
    ClaudeCode,
    PermissionMode,
    claude_code,
    parse_session_usage,
    parse_stream_line,
)
from .codex import (
    Codex,
    CodexEffort,
    codex,
    parse_codex_session_usage,
    parse_codex_stream_line,
)

__all__ = [
    "ClaudeCode",
    "Codex",
    "CodexEffort",
    "PermissionMode",
    "claude_code",
    "codex",
    "parse_codex_session_usage",
    "parse_codex_stream_line",
    "parse_session_usage",
    "parse_stream_line",
]
