"""Branch-strategy unit tests for `worktrees.py`.

Tests the strategy values in isolation — no orchestrator, no sandbox, no agent.
"""

from __future__ import annotations

import subprocess
from pathlib import Path

import pytest

from pysolated import (
    HeadStrategy,
    MergeConflictError,
    MergeToHeadStrategy,
    NamedBranchStrategy,
)


async def test_head_strategy_prepare_returns_cwd_unchanged(tmp_path: Path) -> None:
    """The head strategy is a pass-through: work_dir == cwd, no worktree on disk."""
    strategy = HeadStrategy()
    prepared = await strategy.prepare(str(tmp_path))
    assert prepared.work_dir == str(tmp_path)
    # No worktree directory should have been created.
    assert not (tmp_path / ".pysolated" / "worktrees").exists()


async def test_head_strategy_prepare_does_not_override_target_branch(
    tmp_path: Path,
) -> None:
    """For head, the strategy doesn't pin a target — the orchestrator resolves it."""
    strategy = HeadStrategy()
    prepared = await strategy.prepare(str(tmp_path))
    assert prepared.target_branch is None


async def test_head_strategy_finalize_is_noop_for_success(tmp_path: Path) -> None:
    """A successful run's finalize on head reports nothing preserved."""
    strategy = HeadStrategy()
    prepared = await strategy.prepare(str(tmp_path))
    outcome = await strategy.finalize(prepared, success=True)
    assert outcome.preserved_worktree_path is None


async def test_head_strategy_finalize_is_noop_for_failure(tmp_path: Path) -> None:
    """Even on failure, head has nothing to preserve — there's no worktree."""
    strategy = HeadStrategy()
    prepared = await strategy.prepare(str(tmp_path))
    outcome = await strategy.finalize(prepared, success=False)
    assert outcome.preserved_worktree_path is None


# ---------------------------------------------------------------------------
# MergeToHeadStrategy — tested against a real temporary git repo.
# ---------------------------------------------------------------------------


def _git(cwd: Path, *args: str) -> str:
    result = subprocess.run(
        ["git", *args],
        cwd=cwd,
        check=True,
        capture_output=True,
        text=True,
    )
    return result.stdout


def _git_no_check(cwd: Path, *args: str) -> subprocess.CompletedProcess[str]:
    return subprocess.run(["git", *args], cwd=cwd, capture_output=True, text=True)


@pytest.fixture
def git_repo(tmp_path: Path) -> Path:
    subprocess.run(["git", "init", "-q", "-b", "main"], cwd=tmp_path, check=True)
    _git(tmp_path, "config", "user.email", "test@example.com")
    _git(tmp_path, "config", "user.name", "Test")
    (tmp_path / "seed.txt").write_text("seed\n")
    _git(tmp_path, "add", "seed.txt")
    _git(tmp_path, "commit", "-qm", "seed")
    return tmp_path


async def test_merge_to_head_prepare_creates_worktree_on_temp_branch(
    git_repo: Path,
) -> None:
    """``prepare`` creates a worktree under ``.pysolated/worktrees/`` on a new branch."""
    strategy = MergeToHeadStrategy()
    prepared = await strategy.prepare(str(git_repo))
    # The work_dir must be a path inside the managed worktrees dir, not cwd.
    assert prepared.work_dir != str(git_repo)
    assert Path(prepared.work_dir).is_dir()
    assert ".pysolated/worktrees/" in prepared.work_dir
    # The worktree must be on a temp branch whose name follows the convention.
    branch = _git(Path(prepared.work_dir), "rev-parse", "--abbrev-ref", "HEAD").strip()
    assert branch.startswith("pysolated/")
    # The target branch is the host's current branch (unchanged).
    assert prepared.target_branch == "main"


async def test_merge_to_head_writes_worktrees_gitignore(git_repo: Path) -> None:
    """``.pysolated/worktrees/.gitignore`` is auto-written so worktrees aren't untracked noise."""
    strategy = MergeToHeadStrategy()
    await strategy.prepare(str(git_repo))
    gitignore = git_repo / ".pysolated" / "worktrees" / ".gitignore"
    assert gitignore.exists()
    assert gitignore.read_text().strip() == "*"


async def test_merge_to_head_temp_branch_names_are_unique(git_repo: Path) -> None:
    """Rapid repeated runs must not collide on the same temp branch name."""
    s1 = MergeToHeadStrategy()
    s2 = MergeToHeadStrategy()
    p1 = await s1.prepare(str(git_repo))
    p2 = await s2.prepare(str(git_repo))
    b1 = _git(Path(p1.work_dir), "rev-parse", "--abbrev-ref", "HEAD").strip()
    b2 = _git(Path(p2.work_dir), "rev-parse", "--abbrev-ref", "HEAD").strip()
    assert b1 != b2
    assert p1.work_dir != p2.work_dir


async def test_merge_to_head_clean_run_merges_back_and_cleans_up(
    git_repo: Path,
) -> None:
    """A successful clean run merges the scratch branch back, deletes it, removes the worktree."""
    strategy = MergeToHeadStrategy()
    prepared = await strategy.prepare(str(git_repo))
    worktree = Path(prepared.work_dir)
    temp_branch = _git(worktree, "rev-parse", "--abbrev-ref", "HEAD").strip()
    # Simulate the agent making a commit in the worktree.
    (worktree / "agent.txt").write_text("hello\n")
    _git(worktree, "add", "agent.txt")
    _git(worktree, "commit", "-qm", "agent change")

    finalized = await strategy.finalize(prepared, success=True)

    assert finalized.preserved_worktree_path is None
    # The commit must be on the target branch.
    main_head = _git(git_repo, "rev-parse", "HEAD").strip()
    main_log = _git(git_repo, "log", "--oneline", "-n", "5")
    assert "agent change" in main_log
    # The worktree directory must be gone.
    assert not worktree.exists()
    # The temp branch must be deleted.
    branches = _git(git_repo, "branch", "--list", temp_branch).strip()
    assert branches == "", f"temp branch {temp_branch!r} should be deleted"
    # The host's HEAD is on the target branch (main).
    current = _git(git_repo, "rev-parse", "--abbrev-ref", "HEAD").strip()
    assert current == "main"
    assert main_head  # non-empty


async def test_merge_to_head_no_new_commits_clean_case(git_repo: Path) -> None:
    """A run that produces no commits still cleans up cleanly (no errors, no preserve)."""
    strategy = MergeToHeadStrategy()
    prepared = await strategy.prepare(str(git_repo))
    worktree = Path(prepared.work_dir)
    temp_branch = _git(worktree, "rev-parse", "--abbrev-ref", "HEAD").strip()

    finalized = await strategy.finalize(prepared, success=True)

    assert finalized.preserved_worktree_path is None
    assert not worktree.exists()
    assert _git(git_repo, "branch", "--list", temp_branch).strip() == ""


async def test_merge_to_head_conflict_preserves_and_raises(git_repo: Path) -> None:
    """A merge conflict aborts the merge, preserves the worktree+temp branch,
    and raises ``MergeConflictError`` carrying the worktree path."""
    strategy = MergeToHeadStrategy()
    prepared = await strategy.prepare(str(git_repo))
    worktree = Path(prepared.work_dir)
    temp_branch = _git(worktree, "rev-parse", "--abbrev-ref", "HEAD").strip()

    # Diverge: a conflicting change on the host's target branch AFTER prepare.
    (git_repo / "conflict.txt").write_text("host version\n")
    _git(git_repo, "add", "conflict.txt")
    _git(git_repo, "commit", "-qm", "host change")

    # And a different change to the same file in the worktree.
    (worktree / "conflict.txt").write_text("agent version\n")
    _git(worktree, "add", "conflict.txt")
    _git(worktree, "commit", "-qm", "agent change")

    with pytest.raises(MergeConflictError) as excinfo:
        await strategy.finalize(prepared, success=True)

    err = excinfo.value
    assert err.worktree_path == str(worktree)
    assert err.temp_branch == temp_branch
    # Recovery commands must mention the worktree path and the temp branch.
    rendered = str(err)
    assert worktree.name in rendered or str(worktree) in rendered
    assert temp_branch in rendered

    # Worktree and branch must be preserved.
    assert worktree.exists()
    branches = _git(git_repo, "branch", "--list", temp_branch).strip()
    assert temp_branch in branches
    # The user's tree must not have conflict markers.
    host_text = (git_repo / "conflict.txt").read_text()
    assert "<<<<<<<" not in host_text
    assert ">>>>>>>" not in host_text
    # No in-progress merge state in the user's tree.
    assert not (git_repo / ".git" / "MERGE_HEAD").exists()


async def test_merge_to_head_dirty_worktree_preserved(git_repo: Path) -> None:
    """An otherwise-successful run with uncommitted changes preserves the worktree."""
    strategy = MergeToHeadStrategy()
    prepared = await strategy.prepare(str(git_repo))
    worktree = Path(prepared.work_dir)

    # Leave uncommitted changes (no commit).
    (worktree / "dirty.txt").write_text("uncommitted\n")

    finalized = await strategy.finalize(prepared, success=True)

    assert finalized.preserved_worktree_path == str(worktree)
    assert worktree.exists()
    assert (worktree / "dirty.txt").exists()


async def test_merge_to_head_failure_preserves_worktree(git_repo: Path) -> None:
    """A failed run preserves the worktree regardless of cleanliness."""
    strategy = MergeToHeadStrategy()
    prepared = await strategy.prepare(str(git_repo))
    worktree = Path(prepared.work_dir)
    # A commit, but the run failed.
    (worktree / "agent.txt").write_text("hello\n")
    _git(worktree, "add", "agent.txt")
    _git(worktree, "commit", "-qm", "agent change")

    finalized = await strategy.finalize(prepared, success=False)

    assert finalized.preserved_worktree_path == str(worktree)
    assert worktree.exists()


# ---------------------------------------------------------------------------
# NamedBranchStrategy — tested against a real temporary git repo.
# ---------------------------------------------------------------------------


async def test_named_branch_finalize_keeps_worktree_and_surfaces_path(
    git_repo: Path,
) -> None:
    """Finalize keeps the durable worktree on disk and surfaces its path."""
    strategy = NamedBranchStrategy(branch="feature/a")
    prepared = await strategy.prepare(str(git_repo))
    worktree = Path(prepared.work_dir)

    finalized = await strategy.finalize(prepared, success=True)

    # The durable worktree is *not* deleted — even on a clean successful run.
    assert worktree.exists()
    # It surfaces on `worktree_path` (not `preserved_worktree_path` — that's
    # the merge-to-head exception channel).
    assert finalized.worktree_path == str(worktree)
    assert finalized.preserved_worktree_path is None


async def test_named_branch_rejects_branch_already_in_main_tree(
    git_repo: Path,
) -> None:
    """Targeting the branch checked out in the main tree raises a clear pysolated error."""
    # `main` is already checked out in the main working tree (the fixture seeded it).
    from pysolated import BranchAlreadyCheckedOutError

    strategy = NamedBranchStrategy(branch="main")
    with pytest.raises(BranchAlreadyCheckedOutError) as excinfo:
        await strategy.prepare(str(git_repo))

    err = excinfo.value
    assert err.branch == "main"
    # The error message must mention the branch and not be the raw git output.
    assert "main" in str(err)
    assert "fatal:" not in str(err)


async def test_named_branch_reuses_existing_worktree_and_preserves_dirty_state(
    git_repo: Path,
) -> None:
    """A second run targeting the same branch reuses the worktree, even if dirty.

    Uncommitted work in the durable worktree must never be wiped on reuse.
    """
    strategy = NamedBranchStrategy(branch="feature/z")
    first = await strategy.prepare(str(git_repo))
    worktree = Path(first.work_dir)
    (worktree / "scratch.txt").write_text("unstaged work\n")

    second = await NamedBranchStrategy(branch="feature/z").prepare(str(git_repo))

    assert second.work_dir == first.work_dir
    assert second.worktree_path == first.worktree_path
    assert (Path(second.work_dir) / "scratch.txt").read_text() == "unstaged work\n"


async def test_named_branch_checks_out_existing_local_branch(
    git_repo: Path,
) -> None:
    """An existing local branch with no worktree is checked out into a new worktree."""
    # Create the branch locally on a divergent commit so we can tell which
    # ref the worktree ended up on.
    _git(git_repo, "checkout", "-q", "-b", "feature/y")
    (git_repo / "y.txt").write_text("y\n")
    _git(git_repo, "add", "y.txt")
    _git(git_repo, "commit", "-qm", "y")
    feature_head = _git(git_repo, "rev-parse", "HEAD").strip()
    _git(git_repo, "checkout", "-q", "main")

    strategy = NamedBranchStrategy(branch="feature/y")
    prepared = await strategy.prepare(str(git_repo))

    worktree = Path(prepared.work_dir)
    assert worktree.is_dir()
    # Worktree dir name has slashes replaced with dashes.
    assert worktree.name == "feature-y"
    # The worktree is on the existing branch and at its existing tip.
    assert _git(worktree, "rev-parse", "--abbrev-ref", "HEAD").strip() == "feature/y"
    assert _git(worktree, "rev-parse", "HEAD").strip() == feature_head


async def test_named_branch_creates_new_branch_from_head_when_absent(
    git_repo: Path,
) -> None:
    """A non-existent branch name creates the branch from the host's HEAD."""
    strategy = NamedBranchStrategy(branch="feature/x")
    prepared = await strategy.prepare(str(git_repo))

    # Work dir is the deterministic durable worktree path.
    worktree = Path(prepared.work_dir)
    assert worktree.is_dir()
    # Slashes in the branch name become dashes in the directory name.
    assert worktree == git_repo / ".pysolated" / "worktrees" / "feature-x"
    # The worktree is checked out on the named branch.
    branch_in_worktree = _git(worktree, "rev-parse", "--abbrev-ref", "HEAD").strip()
    assert branch_in_worktree == "feature/x"
    # The branch's tip equals the host's HEAD (it was created from HEAD).
    host_head = _git(git_repo, "rev-parse", "HEAD").strip()
    new_head = _git(worktree, "rev-parse", "HEAD").strip()
    assert new_head == host_head
    # For `branch`, source == target == the named branch.
    assert prepared.target_branch == "feature/x"
    # The durable worktree path is surfaced for the orchestrator to forward.
    assert prepared.worktree_path == str(worktree)


async def test_named_branch_prepare_reports_no_reuse_on_first_run(
    git_repo: Path,
) -> None:
    """A fresh create-or-checkout does not flag reuse — reuse_status is None."""
    prepared = await NamedBranchStrategy(branch="feature/x").prepare(str(git_repo))
    assert prepared.reuse_status is None


async def test_named_branch_prepare_flags_clean_reuse(git_repo: Path) -> None:
    """A second run against an unchanged worktree reports a clean reuse.

    The orchestrator surfaces this as the "log line" the docstring promises;
    here we only care that the strategy itself records the state.
    """
    await NamedBranchStrategy(branch="feature/z").prepare(str(git_repo))
    second = await NamedBranchStrategy(branch="feature/z").prepare(str(git_repo))
    assert second.reuse_status == "clean"


async def test_named_branch_prepare_flags_dirty_reuse(git_repo: Path) -> None:
    """Reuse with uncommitted changes in the worktree reports a dirty reuse.

    The orchestrator surfaces this as the "warning" the docstring promises;
    the strategy itself records the state and never wipes the work.
    """
    first = await NamedBranchStrategy(branch="feature/z").prepare(str(git_repo))
    (Path(first.work_dir) / "scratch.txt").write_text("uncommitted\n")
    second = await NamedBranchStrategy(branch="feature/z").prepare(str(git_repo))
    assert second.reuse_status == "dirty"
    # The work is never wiped.
    assert (Path(second.work_dir) / "scratch.txt").read_text() == "uncommitted\n"


async def test_named_branch_finalize_flags_uncommitted_changes(
    git_repo: Path,
) -> None:
    """Finalize warns when uncommitted changes remain in the worktree.

    The flag is what the orchestrator turns into the user-visible warning;
    the run itself still succeeds.
    """
    strategy = NamedBranchStrategy(branch="feature/x")
    prepared = await strategy.prepare(str(git_repo))
    (Path(prepared.work_dir) / "leftover.txt").write_text("unstaged\n")

    finalized = await strategy.finalize(prepared, success=True)

    assert finalized.dirty_after_run is True
    assert finalized.worktree_path == prepared.worktree_path


async def test_named_branch_finalize_clean_does_not_flag(git_repo: Path) -> None:
    """A clean worktree at finalize doesn't flag dirty-after-run."""
    strategy = NamedBranchStrategy(branch="feature/x")
    prepared = await strategy.prepare(str(git_repo))

    finalized = await strategy.finalize(prepared, success=True)

    assert finalized.dirty_after_run is False
