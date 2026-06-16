"""Display implementations.

`TerminalDisplay` narrates a run to a Rich console; `FileDisplay` writes the
same narration to a log file (so a developer can run unattended and `tail -f`
the log). Both satisfy the `Display` protocol, so the orchestrator works
against either unchanged.
"""

from __future__ import annotations

from pathlib import Path
from typing import TextIO

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
    """Streams a run to a Rich terminal.

    `name` is the optional run name. When set, every status line is prefixed
    with `[<name>]` so concurrent runs in adjacent terminals stay distinguishable.
    """

    def __init__(
        self, console: Console | None = None, name: str | None = None
    ) -> None:
        self._console = console or Console()
        self._name = name

    def intro(self, title: str) -> None:
        self._console.rule(f"[bold]{escape(title)}[/bold]")

    def status(self, message: str, severity: Severity) -> None:
        style = _SEVERITY_STYLE.get(severity, "bold")
        prefix = escape(f"[{self._name}] ") if self._name else ""
        self._console.print(f"[{style}]{prefix}{escape(message)}[/{style}]")

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


class FileDisplay:
    """Streams a run to a log file.

    The file is opened lazily on the first call and held open for the lifetime
    of the display, written line-buffered so `tail -f` shows live progress.
    When `name` is provided, the first line written identifies the run so
    concurrently-running logs are distinguishable at a glance.
    """

    def __init__(self, path: str | Path, name: str | None = None) -> None:
        self._path = str(path)
        self._name = name
        self._file: TextIO | None = None

    @property
    def path(self) -> str:
        return self._path

    def _handle(self) -> TextIO:
        if self._file is None:
            self._file = open(self._path, "w", encoding="utf-8", buffering=1)
            if self._name:
                self._file.write(f"=== run: {self._name} ===\n")
                self._file.flush()
        return self._file

    def intro(self, title: str) -> None:
        self._write(f"=== {title} ===")

    def status(self, message: str, severity: Severity) -> None:
        prefix = f"[{self._name}] " if self._name else ""
        self._write(f"[{severity}] {prefix}{message}")

    def text(self, message: str) -> None:
        self._write(message)

    def tool_call(self, name: str, formatted_args: str) -> None:
        self._write(f"{name}({formatted_args})")

    def summary(self, title: str, rows: dict[str, str]) -> None:
        lines = [f"--- {title} ---"]
        lines.extend(f"  {key}: {value}" for key, value in rows.items())
        lines.append("---")
        self._write("\n".join(lines))

    def _write(self, line: str) -> None:
        handle = self._handle()
        handle.write(line + "\n")
        handle.flush()
