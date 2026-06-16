"""FileDisplay tests — seam-substitution parity with the terminal display."""

from __future__ import annotations

from pathlib import Path

import pytest

from pysolated import Display, FileDisplay


def test_file_display_satisfies_display_protocol(tmp_path: Path) -> None:
    display = FileDisplay(tmp_path / "run.log")
    assert isinstance(display, Display)


def test_file_display_records_all_methods_to_file(tmp_path: Path) -> None:
    log_path = tmp_path / "run.log"
    display = FileDisplay(log_path)

    display.intro("pysolated")
    display.status("Iteration 1/3", "info")
    display.text("hello from the agent")
    display.tool_call("Bash", "ls -la")
    display.summary("Run summary", {"Branch": "main", "Commits": "(none)"})

    contents = log_path.read_text()
    assert "pysolated" in contents
    assert "Iteration 1/3" in contents
    assert "hello from the agent" in contents
    assert "Bash" in contents and "ls -la" in contents
    assert "Run summary" in contents
    assert "Branch" in contents and "main" in contents


def test_file_display_flushes_after_every_write(tmp_path: Path) -> None:
    """A separate reader sees each line as soon as it's written (tail -f).

    Reads the file from disk between writes, which is the same vantage point
    `tail -f` has — if anything is buffered in the FileDisplay's handle the
    reader sees nothing.
    """
    log_path = tmp_path / "run.log"
    display = FileDisplay(log_path)

    display.status("Iteration 1/3", "info")
    assert "Iteration 1/3" in log_path.read_text()

    display.text("agent line A")
    assert "agent line A" in log_path.read_text()


def test_file_display_header_identifies_run_by_name(tmp_path: Path) -> None:
    """The optional run name appears in the file's header (file identity).

    Two concurrent runs writing to differently-named files should still be
    distinguishable by reading their headers alone.
    """
    log_path = tmp_path / "run.log"
    display = FileDisplay(log_path, name="nightly-refactor")
    display.intro("pysolated")

    head = log_path.read_text().splitlines()[0]
    assert "nightly-refactor" in head


def test_file_display_no_name_no_header_noise(tmp_path: Path) -> None:
    """Without a name, the header is still emitted but doesn't fabricate one."""
    log_path = tmp_path / "run.log"
    display = FileDisplay(log_path)
    display.intro("pysolated")
    assert "None" not in log_path.read_text()


def test_file_display_status_prefixes_name(tmp_path: Path) -> None:
    log_path = tmp_path / "run.log"
    display = FileDisplay(log_path, name="alpha")
    display.status("Iteration 1/3", "info")
    line = next(
        line
        for line in log_path.read_text().splitlines()
        if "Iteration 1/3" in line
    )
    assert "[alpha]" in line


def test_file_display_status_no_name_no_prefix(tmp_path: Path) -> None:
    log_path = tmp_path / "run.log"
    display = FileDisplay(log_path)
    display.status("Iteration 1/3", "info")
    line = next(
        line
        for line in log_path.read_text().splitlines()
        if "Iteration 1/3" in line
    )
    assert "[" not in line.replace("[info]", "")
