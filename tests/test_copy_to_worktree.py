"""Tests for `copy_to_worktree=` — host paths copied into a worktree pre-agent.

A worktree is a clean `git checkout`, so gitignored host state (`.env`,
`node_modules`, build artifacts) is absent inside it. `copy_to_worktree=`
reproduces caller-named host paths inside the worktree before the agent runs.

These tests exercise the orchestrator/CLI through their public surfaces — `run()`
and the CLI — against a real temp git repo and the `no_sandbox` provider.
"""

from __future__ import annotations

import json
import os
import subprocess
from pathlib import Path

import pytest

from pysolated import (
    AgentCommandOptions,
    Command,
    HeadStrategy,
    MergeToHeadStrategy,
    NamedBranchStrategy,
    Severity,
    no_sandbox,
    parse_session_usage,
    parse_stream_line,
    run,
)


class _NoopRealAgent:
    """Emits the completion signal so the iteration ends without an agent process."""

    name = "noop"
    env: dict[str, str] = {}

    def build_command(self, options: AgentCommandOptions) -> Command:
        line = json.dumps(
            {
                "type": "assistant",
                "message": {"content": [{"type": "text", "text": "STOP"}]},
            }
        )
        return Command(argv=["printf", "%s\n", line], stdin=None)

    def parse_stream_line(self, line: str):
        return parse_stream_line(line)

    def parse_session_usage(self, content: str):
        return parse_session_usage(content)


class _SilentDisplay:
    def intro(self, title: str) -> None: ...
    def status(self, message: str, severity: Severity) -> None: ...
    def text(self, message: str) -> None: ...
    def tool_call(self, name: str, formatted_args: str) -> None: ...
    def summary(self, title: str, rows: dict[str, str]) -> None: ...


def _git(cwd: Path, *args: str) -> str:
    return subprocess.run(
        ["git", *args], cwd=cwd, check=True, capture_output=True, text=True
    ).stdout


@pytest.fixture
def git_repo(tmp_path: Path) -> Path:
    subprocess.run(["git", "init", "-q", "-b", "main"], cwd=tmp_path, check=True)
    _git(tmp_path, "config", "user.email", "test@example.com")
    _git(tmp_path, "config", "user.name", "Test")
    (tmp_path / "seed.txt").write_text("seed\n")
    _git(tmp_path, "add", "seed.txt")
    _git(tmp_path, "commit", "-qm", "seed")
    return tmp_path


# ---------------------------------------------------------------------------
# Foreign-flag rejection: head + copy_to_worktree.
# ---------------------------------------------------------------------------


async def test_head_strategy_with_copy_to_worktree_raises(git_repo: Path) -> None:
    """`copy_to_worktree` with `HeadStrategy` raises up front — no worktree to copy into."""
    (git_repo / ".env").write_text("SECRET=1\n")
    with pytest.raises(ValueError, match="copy_to_worktree"):
        await run(
            agent=_NoopRealAgent(),
            sandbox=no_sandbox(),
            prompt="go",
            cwd=str(git_repo),
            display=_SilentDisplay(),
            completion_signal="STOP",
            branch_strategy=HeadStrategy(),
            copy_to_worktree=[".env"],
        )


# ---------------------------------------------------------------------------
# Path validation — eager, before strategy.prepare() runs.
# ---------------------------------------------------------------------------


async def test_missing_path_raises_and_leaves_no_worktree(git_repo: Path) -> None:
    """A missing source path errors and never creates a worktree."""
    with pytest.raises(ValueError, match="does not exist"):
        await run(
            agent=_NoopRealAgent(),
            sandbox=no_sandbox(),
            prompt="go",
            cwd=str(git_repo),
            display=_SilentDisplay(),
            completion_signal="STOP",
            branch_strategy=MergeToHeadStrategy(),
            copy_to_worktree=["does-not-exist"],
        )
    # No worktrees dir was created (so the validation truly preceded prepare).
    assert not any((git_repo / ".pysolated" / "worktrees").glob("2*"))


async def test_absolute_path_raises_and_leaves_no_worktree(
    git_repo: Path, tmp_path: Path
) -> None:
    """An absolute source path errors before a worktree is created."""
    abs_path = str(tmp_path / "outside.txt")
    with pytest.raises(ValueError, match="absolute"):
        await run(
            agent=_NoopRealAgent(),
            sandbox=no_sandbox(),
            prompt="go",
            cwd=str(git_repo),
            display=_SilentDisplay(),
            completion_signal="STOP",
            branch_strategy=MergeToHeadStrategy(),
            copy_to_worktree=[abs_path],
        )
    assert not any((git_repo / ".pysolated" / "worktrees").glob("2*"))


async def test_escaping_path_raises_and_leaves_no_worktree(
    git_repo: Path, tmp_path: Path
) -> None:
    """A `..`-escaping path errors before a worktree is created."""
    # Create a sibling file to make the .. target actually exist — otherwise
    # the "does not exist" check could fire first and mask the escape check.
    (tmp_path / "sibling.txt").write_text("sibling\n")
    with pytest.raises(ValueError, match="escaping|inside cwd"):
        await run(
            agent=_NoopRealAgent(),
            sandbox=no_sandbox(),
            prompt="go",
            cwd=str(git_repo),
            display=_SilentDisplay(),
            completion_signal="STOP",
            branch_strategy=MergeToHeadStrategy(),
            copy_to_worktree=["../sibling.txt"],
        )
    assert not any((git_repo / ".pysolated" / "worktrees").glob("2*"))


# ---------------------------------------------------------------------------
# End-to-end copy — files appear inside the worktree before the agent runs.
# ---------------------------------------------------------------------------


class _ReadsFileAgent:
    """Reads `path` inside the work_dir, echoes it back, then signals STOP."""

    name = "reads-file"
    env: dict[str, str] = {}

    def __init__(self, path: str) -> None:
        self._path = path

    def build_command(self, options: AgentCommandOptions) -> Command:
        line = json.dumps(
            {
                "type": "assistant",
                "message": {"content": [{"type": "text", "text": "STOP"}]},
            }
        )
        script = f"cat {self._path} && printf '%s\\n' '{line}'"
        return Command(argv=["sh", "-c", script], stdin=None)

    def parse_stream_line(self, line: str):
        return parse_stream_line(line)

    def parse_session_usage(self, content: str):
        return parse_session_usage(content)


async def test_merge_to_head_copies_file_into_worktree(git_repo: Path) -> None:
    """A merge-to-head run with copy_to_worktree=[".env"] makes .env present in the worktree."""
    (git_repo / ".env").write_text("SECRET=hunter2\n")

    result = await run(
        agent=_ReadsFileAgent(".env"),
        sandbox=no_sandbox(),
        prompt="go",
        cwd=str(git_repo),
        display=_SilentDisplay(),
        completion_signal="STOP",
        branch_strategy=MergeToHeadStrategy(),
        copy_to_worktree=[".env"],
    )
    assert "SECRET=hunter2" in result.stdout


async def test_nested_path_copies_and_creates_parent_dir(git_repo: Path) -> None:
    """A nested source like `config/local.json` works — parent dirs are mkdir -p'd."""
    (git_repo / "config").mkdir()
    (git_repo / "config" / "local.json").write_text('{"k": "v"}\n')

    result = await run(
        agent=_ReadsFileAgent("config/local.json"),
        sandbox=no_sandbox(),
        prompt="go",
        cwd=str(git_repo),
        display=_SilentDisplay(),
        completion_signal="STOP",
        branch_strategy=MergeToHeadStrategy(),
        copy_to_worktree=["config/local.json"],
    )
    assert '"k": "v"' in result.stdout


async def test_directory_with_symlinks_copies_with_symlinks_intact(
    git_repo: Path,
) -> None:
    """A symlink-heavy directory (pnpm-style `node_modules`) copies with symlinks preserved."""
    nm = git_repo / "node_modules"
    nm.mkdir()
    (nm / "real.txt").write_text("real content\n")
    # A relative symlink, the kind a pnpm symlink farm uses.
    os.symlink("real.txt", nm / "alias.txt")

    # Capture the durable worktree path via the `branch` strategy — easier to
    # inspect than the temp `merge-to-head` worktree.
    result = await run(
        agent=_NoopRealAgent(),
        sandbox=no_sandbox(),
        prompt="go",
        cwd=str(git_repo),
        display=_SilentDisplay(),
        completion_signal="STOP",
        branch_strategy=NamedBranchStrategy(branch="feature/syms"),
        copy_to_worktree=["node_modules"],
    )

    assert result.worktree_path is not None
    worktree_nm = Path(result.worktree_path) / "node_modules"
    assert worktree_nm.is_dir()
    alias = worktree_nm / "alias.txt"
    # The symlink must be a symlink in the worktree (not dereferenced).
    assert alias.is_symlink()
    # And it must point at the same target (relative link still resolves).
    assert os.readlink(alias) == "real.txt"
    # The real file is present too.
    assert (worktree_nm / "real.txt").read_text() == "real content\n"


async def test_branch_reuse_overwrites_existing_copy_host_wins(git_repo: Path) -> None:
    """On a `branch` reuse, copy_to_worktree re-copies and overwrites the worktree's copy."""
    (git_repo / ".env").write_text("HOST_VERSION=1\n")

    # First run: durable worktree is created, .env is copied in.
    first = await run(
        agent=_NoopRealAgent(),
        sandbox=no_sandbox(),
        prompt="go",
        cwd=str(git_repo),
        display=_SilentDisplay(),
        completion_signal="STOP",
        branch_strategy=NamedBranchStrategy(branch="feature/reuse"),
        copy_to_worktree=[".env"],
    )
    assert first.worktree_path is not None
    worktree_env = Path(first.worktree_path) / ".env"
    assert worktree_env.read_text() == "HOST_VERSION=1\n"

    # The user hand-edited the worktree's copy AND updated the host.
    worktree_env.write_text("LOCAL_EDIT=2\n")
    (git_repo / ".env").write_text("HOST_VERSION=2\n")

    # Second run: the durable worktree is reused; the copy overwrites the
    # worktree's `.env` with the host's current `.env`. Host wins (ADR 0009).
    second = await run(
        agent=_NoopRealAgent(),
        sandbox=no_sandbox(),
        prompt="go",
        cwd=str(git_repo),
        display=_SilentDisplay(),
        completion_signal="STOP",
        branch_strategy=NamedBranchStrategy(branch="feature/reuse"),
        copy_to_worktree=[".env"],
    )
    assert second.worktree_path == first.worktree_path
    assert worktree_env.read_text() == "HOST_VERSION=2\n"
