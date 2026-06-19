"""Shared stream-parsing helpers used by multiple agent providers.

Claude Code — and the later Copilot provider — both emit stream-json with an
`assistant` content-block shape; this module centralises that parsing and the
allowlist of tool names whose input fields we are willing to surface.
"""

from __future__ import annotations

from ..core import StreamEvent, TextEvent, ToolCallEvent

# Allowlisted tools, mapped to the input field carrying the display arg.
# Anything not listed here is dropped — we never surface arbitrary tool input.
TOOL_ARG_FIELDS: dict[str, str] = {
    "Bash": "command",
    "WebSearch": "query",
    "WebFetch": "url",
    "Agent": "description",
}


def parse_assistant_content_blocks(content: list[object]) -> list[StreamEvent]:
    """Decode an `assistant` message's `content` blocks into stream events.

    Pure. Text blocks concatenate into `TextEvent`s; allowlisted `tool_use`
    blocks become `ToolCallEvent`s. Pending text is flushed before each tool
    call so events stay in source order. Unknown / wrong-typed blocks are
    silently skipped.
    """
    events: list[StreamEvent] = []
    texts: list[str] = []
    for block in content:
        if not isinstance(block, dict):
            continue
        block_type = block.get("type")
        if block_type == "text" and isinstance(block.get("text"), str):
            texts.append(block["text"])
        elif (
            block_type == "tool_use"
            and isinstance(block.get("name"), str)
            and isinstance(block.get("input"), dict)
        ):
            arg_field = TOOL_ARG_FIELDS.get(block["name"])
            if arg_field is None:
                continue  # not allowlisted
            arg_value = block["input"].get(arg_field)
            if not isinstance(arg_value, str):
                continue  # missing / wrong-typed arg field
            if texts:
                events.append(TextEvent(text="".join(texts)))
                texts = []
            events.append(ToolCallEvent(name=block["name"], args=arg_value))
    if texts:
        events.append(TextEvent(text="".join(texts)))
    return events
