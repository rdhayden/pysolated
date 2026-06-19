"""Branch strategies — how a `run()` places its git work.

A **branch strategy** is a closed value union (ADR 0007), not a user-pluggable
Protocol seam. Two strategies live here:

- ``HeadStrategy`` — commit directly on the host's current branch. The default.
  Trivial pass-through: the iteration loop runs in ``cwd`` itself, no worktree
  is created, no merge happens.
- ``MergeToHeadStrategy`` — run the agent in a worktree on a temporary scratch
  branch and merge that back to the host's current branch when the run
  finishes. Worktree + branch are preserved on a merge conflict or when
  uncommitted changes remain — work is never silently discarded.

The strategy interface is two host-side hooks bracketing the sandbox lifetime:
``prepare(cwd)`` runs before ``sandbox.create()`` (so a worktree, if any, exists
before the sandbox tries to access it), and ``finalize(prepared, success)``
runs after ``sandbox.close()`` (so a merge-back happens on the host, not
through the sandbox seam — which couldn't reach it anyway, since the sandbox is
gone by then). See ADR 0007 for why this bracket lives host-side rather than
routing through ``sandbox.exec``.
"""

from __future__ import annotations

import asyncio
import secrets
import shutil
import subprocess
from dataclasses import dataclass
from datetime import datetime
from pathlib import Path
from typing import Literal

from .errors import BranchAlreadyCheckedOutError, MergeConflictError

ReuseStatus = Literal["clean", "dirty"]


@dataclass(frozen=True)
class PreparedRun:
    """What a branch strategy hands back to the orchestrator pre-sandbox.

    ``work_dir`` is the directory the iteration loop runs in — ``cwd`` for
    ``head``; the worktree path for ``merge-to-head``. ``target_branch`` is the
    branch work *lands* on (= ``RunResult.branch``). ``None`` means "the
    orchestrator resolves it through the sandbox seam from ``work_dir``" — which
    is the trivial case for ``head`` (target == source == HEAD of ``cwd``).

    ``worktree_path`` and ``temp_branch`` are set only by ``MergeToHeadStrategy``
    so its own ``finalize`` can find what to merge back and clean up. The
    orchestrator treats them as opaque.

    ``reuse_status`` is set by ``NamedBranchStrategy`` when an existing durable
    worktree was reused — ``"clean"`` when no uncommitted changes were present
    (orchestrator emits an info log line) and ``"dirty"`` when uncommitted work
    was found (orchestrator emits a warning). ``None`` for fresh creates,
    checkouts of an existing branch, and the other strategies.
    """

    work_dir: str
    target_branch: str | None = None
    worktree_path: str | None = None
    temp_branch: str | None = None
    reuse_status: ReuseStatus | None = None


@dataclass(frozen=True)
class FinalizedRun:
    """What a branch strategy reports back post-sandbox.

    ``preserved_worktree_path`` is set when the strategy decided to leave a
    worktree on disk rather than removing it (a merge-conflict, or uncommitted
    changes on an otherwise-successful run). ``None`` when nothing was
    preserved. ``head`` never preserves — there is no worktree to leave behind.

    ``worktree_path`` is set when the strategy ran in a **durable worktree**
    that persists by design (``NamedBranchStrategy``). It is distinct from
    ``preserved_worktree_path`` (the exceptional "kept because something went
    wrong" channel of ``MergeToHeadStrategy``); the two fields are never both
    set on the same finalize. See ADR 0008.

    ``dirty_after_run`` is set by ``NamedBranchStrategy`` when the durable
    worktree still holds uncommitted changes after the agent's run. The run
    itself still succeeds — the flag is how the strategy asks the orchestrator
    to surface a warning that left-over work is in the worktree.
    """

    preserved_worktree_path: str | None = None
    worktree_path: str | None = None
    dirty_after_run: bool = False


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

    async def finalize(self, prepared: PreparedRun, *, success: bool) -> FinalizedRun:
        return FinalizedRun(preserved_worktree_path=None)


@dataclass(frozen=True)
class MergeToHeadStrategy:
    """Run the agent in a worktree on a scratch branch; merge back to the host.

    ``prepare`` creates a worktree under ``.pysolated/worktrees/`` on a new
    ``pysolated/<YYYYMMDD-HHMMSS>-<rand>`` branch checked out from the host's
    current branch. ``finalize`` decides:

    - clean run with new commits → merge the scratch branch back into the
      target, delete the scratch branch, remove the worktree;
    - clean run with no new commits → cleanup only;
    - **merge conflict** → ``git merge --abort`` in the user's tree, preserve
      the worktree + scratch branch, raise ``MergeConflictError`` with the
      worktree path and recovery commands;
    - **uncommitted changes** left in the worktree on a successful run →
      preserve the worktree, surface its path on the result;
    - failed run (``success=False``) → preserve the worktree so the agent's
      work can be recovered.

    The strategy value is frozen and reusable; per-run state lives in
    ``PreparedRun`` (the worktree path + scratch branch name).
    """

    async def prepare(self, cwd: str) -> PreparedRun:
        repo = Path(cwd)
        worktrees_root = repo / ".pysolated" / "worktrees"
        worktrees_root.mkdir(parents=True, exist_ok=True)
        gitignore = worktrees_root / ".gitignore"
        if not gitignore.exists():
            gitignore.write_text("*\n")

        suffix = _temp_suffix()
        temp_branch = f"pysolated/{suffix}"
        worktree_path = worktrees_root / suffix
        target_branch = await _current_branch(repo)

        # `git worktree add -b <new_branch> <path> <start_point>` creates the
        # scratch branch from the host's HEAD and checks it out into the
        # worktree directory in one shot.
        await _run_git(
            repo,
            "worktree",
            "add",
            "-b",
            temp_branch,
            str(worktree_path),
            "HEAD",
        )

        return PreparedRun(
            work_dir=str(worktree_path),
            target_branch=target_branch,
            worktree_path=str(worktree_path),
            temp_branch=temp_branch,
        )

    async def finalize(self, prepared: PreparedRun, *, success: bool) -> FinalizedRun:
        worktree_path = prepared.worktree_path
        temp_branch = prepared.temp_branch
        target_branch = prepared.target_branch
        # Defensive — prepare always sets these for merge-to-head.
        assert worktree_path is not None
        assert temp_branch is not None
        assert target_branch is not None

        repo = Path(worktree_path).parent.parent.parent
        worktree = Path(worktree_path)

        # A failed run preserves the worktree and the scratch branch so the
        # agent's work can be recovered by hand.
        if not success:
            return FinalizedRun(preserved_worktree_path=worktree_path)

        # Uncommitted changes in the worktree are never deleted. Preserve and
        # surface the path; the run keeps reporting success.
        if await _worktree_is_dirty(worktree):
            return FinalizedRun(preserved_worktree_path=worktree_path)

        # Attempt the merge into the user's tree. A successful merge cleans
        # everything up; a conflict aborts the merge in the user's tree and
        # preserves the worktree + scratch branch with a recovery error.
        merge_result = await _run_git_capture(
            repo,
            "merge",
            "--no-ff",
            "--no-edit",
            temp_branch,
        )
        if merge_result.returncode != 0:
            # Roll the user's tree back to a clean state so no conflict
            # markers are left behind. The scratch branch and worktree stay
            # on disk — the user finishes the merge by hand from there.
            await _run_git_capture(repo, "merge", "--abort")
            raise MergeConflictError(
                worktree_path=worktree_path,
                temp_branch=temp_branch,
                target_branch=target_branch,
            )

        # Clean merge — tear down the worktree and the scratch branch.
        await _run_git_capture(repo, "worktree", "remove", "--force", worktree_path)
        # The worktree dir is usually gone after `worktree remove`, but the
        # parent suffix directory may linger empty; remove it if present.
        if worktree.exists():
            shutil.rmtree(worktree, ignore_errors=True)
        await _run_git_capture(repo, "branch", "-D", temp_branch)

        return FinalizedRun(preserved_worktree_path=None)


@dataclass(frozen=True)
class NamedBranchStrategy:
    """Run the agent in a durable worktree on a caller-named branch.

    ``prepare`` locates or creates a worktree under ``.pysolated/worktrees/``
    on the named branch. Create-or-checkout-or-reuse, in order:

    - existing worktree at the deterministic path → **reuse** (clean → log,
      dirty → warn); uncommitted work is never wiped;
    - existing local branch with no worktree → check it out into a new
      worktree;
    - branch does not exist → create it from the host's current ``HEAD``.

    ``finalize`` keeps the worktree on disk by design — there is **no
    merge-back** — and surfaces the durable worktree path so the orchestrator
    can copy it onto ``RunResult.worktree_path``. A run with uncommitted
    changes left behind in the worktree warns but still reports success.

    For ``branch``, source == target == the named branch. The worktree
    directory name is the branch with slashes mapped to dashes (e.g.
    ``feature/x`` → ``feature-x``); collision caveat documented in ADR 0008.
    """

    branch: str

    async def prepare(self, cwd: str) -> PreparedRun:
        repo = Path(cwd)
        worktrees_root = repo / ".pysolated" / "worktrees"
        worktrees_root.mkdir(parents=True, exist_ok=True)
        gitignore = worktrees_root / ".gitignore"
        if not gitignore.exists():
            gitignore.write_text("*\n")

        worktree_path = worktrees_root / self.branch.replace("/", "-")

        if worktree_path.exists():
            # Reuse the existing durable worktree as-is — uncommitted work is
            # never wiped. Surface clean vs dirty so the orchestrator can log
            # vs warn (ADR 0008).
            dirty = await _worktree_is_dirty(worktree_path)
            return PreparedRun(
                work_dir=str(worktree_path),
                target_branch=self.branch,
                worktree_path=str(worktree_path),
                reuse_status="dirty" if dirty else "clean",
            )

        if await _branch_exists_locally(repo, self.branch):
            if await _branch_is_checked_out(repo, self.branch):
                raise BranchAlreadyCheckedOutError(branch=self.branch)
            await _run_git(
                repo,
                "worktree",
                "add",
                str(worktree_path),
                self.branch,
            )
        else:
            await _run_git(
                repo,
                "worktree",
                "add",
                "-b",
                self.branch,
                str(worktree_path),
                "HEAD",
            )

        return PreparedRun(
            work_dir=str(worktree_path),
            target_branch=self.branch,
            worktree_path=str(worktree_path),
        )

    async def finalize(self, prepared: PreparedRun, *, success: bool) -> FinalizedRun:
        # Durable worktree: keep on disk by design, no merge-back. The
        # orchestrator surfaces the path on `RunResult.worktree_path`. If the
        # worktree has uncommitted changes after the run, flag it so the
        # orchestrator emits a warning — the run still reports success.
        worktree_path = prepared.worktree_path
        assert worktree_path is not None
        dirty = await _worktree_is_dirty(Path(worktree_path))
        return FinalizedRun(
            preserved_worktree_path=None,
            worktree_path=worktree_path,
            dirty_after_run=dirty,
        )


BranchStrategy = HeadStrategy | MergeToHeadStrategy | NamedBranchStrategy
"""The strategy union. ``HeadStrategy`` is the default; ``MergeToHeadStrategy``
runs the agent in a worktree on a scratch branch and merges it back;
``NamedBranchStrategy`` runs in a durable worktree on a caller-named branch
with no merge-back."""


# ---------------------------------------------------------------------------
# Internal helpers — host-side git invocations.
# ---------------------------------------------------------------------------


def _temp_suffix() -> str:
    """``<YYYYMMDD-HHMMSS>-<rand>`` — random tail avoids sub-second collisions."""
    ts = datetime.now().strftime("%Y%m%d-%H%M%S")
    rand = secrets.token_hex(3)
    return f"{ts}-{rand}"


async def _current_branch(cwd: Path) -> str:
    result = await _run_git_capture(cwd, "rev-parse", "--abbrev-ref", "HEAD")
    if result.returncode != 0:
        return ""
    return result.stdout.strip()


async def _branch_exists_locally(cwd: Path, branch: str) -> bool:
    """True when ``branch`` is a known local branch in the repo at ``cwd``."""
    result = await _run_git_capture(
        cwd, "show-ref", "--verify", "--quiet", f"refs/heads/{branch}"
    )
    return result.returncode == 0


async def _branch_is_checked_out(cwd: Path, branch: str) -> bool:
    """True when ``branch`` is the checked-out branch of any existing worktree.

    ``git worktree list --porcelain`` emits a ``branch refs/heads/<name>`` line
    for every worktree that has a branch checked out. The main working tree is
    one of those entries — that is the case ADR 0008's "already checked out"
    error guards against.
    """
    result = await _run_git_capture(cwd, "worktree", "list", "--porcelain")
    if result.returncode != 0:
        return False
    needle = f"branch refs/heads/{branch}"
    for line in result.stdout.splitlines():
        if line.strip() == needle:
            return True
    return False


async def _worktree_is_dirty(worktree: Path) -> bool:
    """True when the worktree has uncommitted (staged or unstaged) changes."""
    result = await _run_git_capture(worktree, "status", "--porcelain")
    return result.returncode == 0 and result.stdout.strip() != ""


@dataclass
class _GitResult:
    returncode: int
    stdout: str
    stderr: str


async def _run_git_capture(cwd: Path, *args: str) -> _GitResult:
    """Run ``git`` with stdout/stderr captured; never raises on a non-zero exit."""
    proc = await asyncio.create_subprocess_exec(
        "git",
        *args,
        cwd=str(cwd),
        stdout=asyncio.subprocess.PIPE,
        stderr=asyncio.subprocess.PIPE,
    )
    stdout_b, stderr_b = await proc.communicate()
    return _GitResult(
        returncode=proc.returncode or 0,
        stdout=stdout_b.decode("utf-8", errors="replace"),
        stderr=stderr_b.decode("utf-8", errors="replace"),
    )


async def _run_git(cwd: Path, *args: str) -> _GitResult:
    """Run ``git`` and raise on a non-zero exit (used for prepare's setup)."""
    result = await _run_git_capture(cwd, *args)
    if result.returncode != 0:
        raise subprocess.CalledProcessError(
            result.returncode,
            ["git", *args],
            output=result.stdout,
            stderr=result.stderr,
        )
    return result


__all__ = [
    "BranchStrategy",
    "FinalizedRun",
    "HeadStrategy",
    "MergeToHeadStrategy",
    "NamedBranchStrategy",
    "PreparedRun",
]
