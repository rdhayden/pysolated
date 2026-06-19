"""Branch-strategy unit tests for `worktrees.py`.

Tests the strategy values in isolation — no orchestrator, no sandbox, no agent.
"""

from __future__ import annotations

from pathlib import Path

from pysolated import HeadStrategy


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
    await strategy.prepare(str(tmp_path))
    outcome = await strategy.finalize(success=True)
    assert outcome.preserved_worktree_path is None


async def test_head_strategy_finalize_is_noop_for_failure(tmp_path: Path) -> None:
    """Even on failure, head has nothing to preserve — there's no worktree."""
    strategy = HeadStrategy()
    await strategy.prepare(str(tmp_path))
    outcome = await strategy.finalize(success=False)
    assert outcome.preserved_worktree_path is None
