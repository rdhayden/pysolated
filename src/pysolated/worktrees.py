"""Branch strategies — how a `run()` places its git work.

A **branch strategy** is a closed value union (ADR 0007), not a user-pluggable
Protocol seam. Two strategies will land in this module:

- ``HeadStrategy`` — commit directly on the host's current branch. The default.
  Trivial pass-through: the iteration loop runs in ``cwd`` itself, no worktree
  is created, no merge happens.
- ``MergeToHeadStrategy`` — *(arrives in a follow-up slice)* run the agent in a
  worktree on a temporary scratch branch and merge that back to the host's
  current branch when the run finishes.

The strategy interface is two host-side hooks bracketing the sandbox lifetime:
``prepare(cwd)`` runs before ``sandbox.create()`` (so a worktree, if any, exists
before the sandbox tries to access it), and ``finalize(success)`` runs after
``sandbox.close()`` (so a merge-back happens on the host, not through the
sandbox seam — which couldn't reach it anyway, since the sandbox is gone by
then). See ADR 0007 for why this bracket lives host-side rather than routing
through ``sandbox.exec``.
"""

from __future__ import annotations

from dataclasses import dataclass


@dataclass(frozen=True)
class PreparedRun:
    """What a branch strategy hands back to the orchestrator pre-sandbox.

    ``work_dir`` is the directory the iteration loop runs in — ``cwd`` for
    ``head``; the worktree path for ``merge-to-head``. ``target_branch`` is the
    branch work *lands* on (= ``RunResult.branch``). ``None`` means "the
    orchestrator resolves it through the sandbox seam from ``work_dir``" — which
    is the trivial case for ``head`` (target == source == HEAD of ``cwd``).
    """

    work_dir: str
    target_branch: str | None = None


@dataclass(frozen=True)
class FinalizedRun:
    """What a branch strategy reports back post-sandbox.

    ``preserved_worktree_path`` is set when the strategy decided to leave a
    worktree on disk rather than removing it (a merge-conflict, or uncommitted
    changes on an otherwise-successful run). ``None`` when nothing was
    preserved. ``head`` never preserves — there is no worktree to leave behind.
    """

    preserved_worktree_path: str | None = None


@dataclass(frozen=True)
class HeadStrategy:
    """Commit directly on the host's current branch — no worktree, no merge.

    The default. Iterations run in ``cwd`` itself; branch and commit resolution
    happen through the sandbox seam exactly as they always have. ``prepare`` is
    a trivial pass-through and ``finalize`` is a no-op — so a ``run()`` called
    without ``branch_strategy=`` (or with ``HeadStrategy()``) behaves
    byte-for-byte as it did before the strategy seam was introduced.
    """

    async def prepare(self, cwd: str) -> PreparedRun:
        return PreparedRun(work_dir=cwd, target_branch=None)

    async def finalize(self, *, success: bool) -> FinalizedRun:
        return FinalizedRun(preserved_worktree_path=None)


BranchStrategy = HeadStrategy
"""The strategy union. Will widen to ``HeadStrategy | MergeToHeadStrategy`` when
the second strategy lands; until then it's a single-arm alias so callers and
type annotations can already speak the union name."""


__all__ = [
    "BranchStrategy",
    "FinalizedRun",
    "HeadStrategy",
    "PreparedRun",
]
