"""TerminalDisplay tests — focused on observable rendered text.

We capture output through Rich's `Console(file=...)` so the assertions look at
what a user sees in the terminal without coupling to Rich's markup tokens.
"""

from __future__ import annotations

from io import StringIO

from rich.console import Console

from pysolated import TerminalDisplay


def _console() -> tuple[Console, StringIO]:
    buf = StringIO()
    return Console(file=buf, force_terminal=False, color_system=None, width=120), buf


def test_terminal_display_status_prefixes_name() -> None:
    console, buf = _console()
    display = TerminalDisplay(console=console, name="alpha")
    display.status("Iteration 1/3", "info")
    assert "[alpha] Iteration 1/3" in buf.getvalue()


def test_terminal_display_status_no_name_no_prefix() -> None:
    console, buf = _console()
    display = TerminalDisplay(console=console)
    display.status("Iteration 1/3", "info")
    out = buf.getvalue()
    assert "Iteration 1/3" in out
    assert "[" not in out  # no name prefix when name is absent
