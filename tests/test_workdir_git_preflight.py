"""Pre-flight warning when the work dir is not a git repo inside the sandbox.

pysolated supports running outside a git repo, so this is a warning rather
than a failure — but a *silent* degradation is a footgun (the usual cause is
mounting a subdirectory of a repo, which hides `.git`, and git/`gh` then fail
with a confusing "not a git repository" error far downstream). The orchestrator
surfaces it once, at run start. These tests pin that behavior against the real
`no_sandbox` provider so the git probe actually runs.
"""

from __future__ import annotations

import json
import subprocess
from pathlib import Path

import pytest

from pysolated import (
    AgentCommandOptions,
    Command,
    Severity,
    StreamEvent,
    Usage,
    no_sandbox,
    parse_session_usage,
    parse_stream_line,
    run,
)


class NoopAgent:
    """Returns a `printf` command emitting the completion signal, nothing else."""

    name = "noop"
    env: dict[str, str] = {}

    def build_command(self, options: AgentCommandOptions) -> Command:
        line = json.dumps(
            {
                "type": "assistant",
                "message": {"content": [{"type": "text", "text": "done STOP-NOW"}]},
            }
        )
        return Command(argv=["printf", "%s\n", line], stdin=None)

    def parse_stream_line(self, line: str) -> list[StreamEvent]:
        return parse_stream_line(line)

    def parse_session_usage(self, content: str) -> Usage | None:
        return parse_session_usage(content)


class RecordingDisplay:
    """Captures every `status` call so tests can assert on warnings."""

    def __init__(self) -> None:
        self.statuses: list[tuple[str, Severity]] = []

    def intro(self, title: str) -> None: ...

    def status(self, message: str, severity: Severity) -> None:
        self.statuses.append((message, severity))

    def text(self, message: str) -> None: ...
    def tool_call(self, name: str, formatted_args: str) -> None: ...
    def summary(self, title: str, rows: dict[str, str]) -> None: ...

    def warnings(self) -> list[str]:
        return [msg for msg, sev in self.statuses if sev == "warn"]


@pytest.fixture
def git_repo(tmp_path: Path) -> Path:
    subprocess.run(["git", "init", "-q", "-b", "main"], cwd=tmp_path, check=True)
    subprocess.run(
        ["git", "config", "user.email", "test@example.com"], cwd=tmp_path, check=True
    )
    subprocess.run(["git", "config", "user.name", "Test"], cwd=tmp_path, check=True)
    (tmp_path / "seed.txt").write_text("seed\n")
    subprocess.run(["git", "add", "seed.txt"], cwd=tmp_path, check=True)
    subprocess.run(["git", "commit", "-qm", "seed"], cwd=tmp_path, check=True)
    return tmp_path


async def test_warns_when_work_dir_is_not_a_git_repo(tmp_path: Path) -> None:
    display = RecordingDisplay()

    await run(
        agent=NoopAgent(),
        sandbox=no_sandbox(),
        prompt="go",
        cwd=str(tmp_path),  # a bare temp dir — not a git repo
        display=display,
        completion_signal="STOP-NOW",
    )

    assert any("not a git repository" in w for w in display.warnings())


async def test_no_warning_inside_a_git_repo(git_repo: Path) -> None:
    display = RecordingDisplay()

    await run(
        agent=NoopAgent(),
        sandbox=no_sandbox(),
        prompt="go",
        cwd=str(git_repo),
        display=display,
        completion_signal="STOP-NOW",
    )

    assert not any("not a git repository" in w for w in display.warnings())
