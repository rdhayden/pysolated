"""Commit collection tests against a real temporary git repo.

A `run()` records HEAD before invoking the agent and then returns the SHAs of
commits created between that point and the post-run HEAD. These tests use the
real `no_sandbox` provider plus a tiny stub agent so the git commands actually
hit a real repo on disk.
"""

from __future__ import annotations

import asyncio
import json
import subprocess
from pathlib import Path
from typing import Callable

import pytest

from pysolated import (
    AgentCommandOptions,
    Command,
    ExecResult,
    Severity,
    no_sandbox,
    parse_session_usage,
    parse_stream_line,
    run,
)


class NoopAgent:
    """Returns a no-op `true` command so the sandbox exec is real but silent."""

    name = "noop"
    env: dict[str, str] = {}

    def build_command(self, options: AgentCommandOptions) -> Command:
        # Emit one assistant line containing the configured completion signal,
        # so the run exits the iteration loop cleanly via that signal instead
        # of via the idle timeout. `printf` is portable across the platforms
        # the CI runs on.
        line = json.dumps(
            {
                "type": "assistant",
                "message": {"content": [{"type": "text", "text": "done STOP-NOW"}]},
            }
        )
        return Command(argv=["printf", "%s\n", line], stdin=None)

    def parse_stream_line(self, line: str):
        return parse_stream_line(line)

    def parse_session_usage(self, content: str):
        return parse_session_usage(content)


class CommittingAgent(NoopAgent):
    """Creates `n` real commits in `cwd` when invoked.

    Models the v1 head-strategy contract: commits land directly on the host's
    current branch.
    """

    def __init__(self, cwd: Path, n: int) -> None:
        self._cwd = cwd
        self._n = n
        self._invocations = 0

    def build_command(self, options: AgentCommandOptions) -> Command:
        # Use the parent's stream line so the orchestrator still sees the
        # completion signal, but stuff a few real commits in first via a tiny
        # shell snippet executed by the sandbox.
        self._invocations += 1
        for i in range(self._n):
            path = self._cwd / f"f_{self._invocations}_{i}.txt"
            path.write_text("hello\n")
            subprocess.run(
                ["git", "-C", str(self._cwd), "add", path.name], check=True
            )
            subprocess.run(
                ["git", "-C", str(self._cwd), "commit", "-m", f"add {path.name}"],
                check=True,
            )
        return super().build_command(options)


class _SilentDisplay:
    def intro(self, title: str) -> None: ...
    def status(self, message: str, severity: Severity) -> None: ...
    def text(self, message: str) -> None: ...
    def tool_call(self, name: str, formatted_args: str) -> None: ...
    def summary(self, title: str, rows: dict[str, str]) -> None: ...


@pytest.fixture
def git_repo(tmp_path: Path) -> Path:
    """A minimal local git repo with one seed commit."""
    subprocess.run(["git", "init", "-q", "-b", "main"], cwd=tmp_path, check=True)
    subprocess.run(
        ["git", "config", "user.email", "test@example.com"], cwd=tmp_path, check=True
    )
    subprocess.run(
        ["git", "config", "user.name", "Test"], cwd=tmp_path, check=True
    )
    (tmp_path / "seed.txt").write_text("seed\n")
    subprocess.run(["git", "add", "seed.txt"], cwd=tmp_path, check=True)
    subprocess.run(["git", "commit", "-qm", "seed"], cwd=tmp_path, check=True)
    return tmp_path


async def test_commits_empty_when_agent_does_not_commit(git_repo: Path) -> None:
    result = await run(
        agent=NoopAgent(),
        sandbox=no_sandbox(),
        prompt="go",
        cwd=str(git_repo),
        display=_SilentDisplay(),
        completion_signal="STOP-NOW",
    )
    assert result.commits == []


async def test_commits_lists_only_new_commits_not_pre_existing_history(
    git_repo: Path,
) -> None:
    # Pre-existing history: an extra commit before the run starts. It must NOT
    # appear in result.commits.
    (git_repo / "before.txt").write_text("before\n")
    subprocess.run(["git", "add", "before.txt"], cwd=git_repo, check=True)
    subprocess.run(["git", "commit", "-qm", "before-run"], cwd=git_repo, check=True)
    pre_run_shas = _all_shas(git_repo)

    result = await run(
        agent=CommittingAgent(git_repo, n=2),
        sandbox=no_sandbox(),
        prompt="go",
        cwd=str(git_repo),
        display=_SilentDisplay(),
        completion_signal="STOP-NOW",
    )

    assert len(result.commits) == 2
    # None of the new SHAs may overlap pre-existing history.
    assert not (set(result.commits) & set(pre_run_shas))
    # Every reported SHA is a real commit in the repo.
    for sha in result.commits:
        assert sha in _all_shas(git_repo)


async def test_commits_returned_in_rev_list_order_newest_first(git_repo: Path) -> None:
    # `git rev-list` returns commits in reverse-chronological order, which is
    # what the orchestrator forwards as `RunResult.commits`. Three commits in
    # one iteration → the last one written is index 0.
    result = await run(
        agent=CommittingAgent(git_repo, n=3),
        sandbox=no_sandbox(),
        prompt="go",
        cwd=str(git_repo),
        display=_SilentDisplay(),
        completion_signal="STOP-NOW",
    )
    assert len(result.commits) == 3
    head = subprocess.run(
        ["git", "rev-parse", "HEAD"],
        cwd=git_repo,
        capture_output=True,
        text=True,
        check=True,
    ).stdout.strip()
    assert result.commits[0] == head


def _all_shas(repo: Path) -> list[str]:
    result = subprocess.run(
        ["git", "log", "--format=%H"],
        cwd=repo,
        capture_output=True,
        text=True,
        check=True,
    )
    return [s for s in result.stdout.splitlines() if s]
