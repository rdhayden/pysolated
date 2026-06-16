"""Terminal display — the only `Display` impl in this slice.

Renders the run to a Rich console. Agent-supplied text and tool args are escaped
so their literal `[...]` content is never interpreted as Rich markup.
"""

from __future__ import annotations

from rich.console import Console
from rich.markup import escape
from rich.panel import Panel

from .core import Severity

_SEVERITY_STYLE: dict[Severity, str] = {
    "info": "bold",
    "success": "bold green",
    "warn": "bold yellow",
    "error": "bold red",
}


class TerminalDisplay:
    """Streams a run to a Rich terminal."""

    def __init__(self, console: Console | None = None) -> None:
        self._console = console or Console()

    def intro(self, title: str) -> None:
        self._console.rule(f"[bold]{escape(title)}[/bold]")

    def status(self, message: str, severity: Severity) -> None:
        style = _SEVERITY_STYLE.get(severity, "bold")
        self._console.print(f"[{style}]{escape(message)}[/{style}]")

    def text(self, message: str) -> None:
        self._console.print(escape(message))

    def tool_call(self, name: str, formatted_args: str) -> None:
        self._console.print(f"[dim]{escape(name)}({escape(formatted_args)})[/dim]")

    def summary(self, title: str, rows: dict[str, str]) -> None:
        body = "\n".join(
            f"[bold]{escape(key)}[/bold]: [dim]{escape(value)}[/dim]"
            for key, value in rows.items()
        )
        self._console.print(Panel(body, title=escape(title), expand=False))
