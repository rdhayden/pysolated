# Branching, worktrees & sync

Roadmap item 4 (worktrees + `copyToWorktree`). See [features.md](./features.md) for
the index.

- **Branch strategies** — `head` (v1 only) plus **merge-to-head** (temp branch →
  agent works → merged back to HEAD) and **branch** (commits land on an explicit
  named branch).
- **Worktrees** — git worktrees under `.sandcastle/worktrees/`; reuse-by-default
  (ADR 0003) and worktree locking (ADR 0007).
- **`createWorktree()`** — public API to create/own a worktree explicitly and scope
  `run()`/`interactive()`/`createSandbox()` to it.
- **`copyToWorktree`** — copy specified host paths into the worktree before the
  sandbox starts.
- **Source/target branch concepts** and the branch-derived built-in prompt args
  (`SOURCE_BRANCH`, `TARGET_BRANCH`).
- **Sync in / sync out** for isolated providers, with the sandbox-owned sync base
  ref (ADR 0017) — moving code into the sandbox and pulling commits back out.
- **Preserved worktree path** on the result when a successful run leaves
  uncommitted changes behind.

---

## Committed slice scope (tracer bullet: branch-strategy seam + `merge-to-head`)

Settled in a grilling session (2026-06-19). The bullet proves *a run's git work
can be placed via a selectable branch strategy* — the strategy seam plus **one**
non-trivial strategy (**`merge-to-head`**, chosen because it stresses the seam
hardest: temp-branch naming, worktree create, **running iterations in the worktree
instead of `cwd`**, commit detection, merge-back, cleanup, and the conflict /
dirty preservation paths). `branch` (named) and the standalone `createWorktree()`
handle are fast-follow repetitions.

**A key realization narrowed the slice:** all three current providers
(`no_sandbox`, `podman`, `docker`) are **bind-mount style** — they mount the host
repo. pysolated has **no isolated provider**, so **sync-in / sync-out and the
sandbox-owned sync base ref (ADR 0017) have no provider to serve** and are
structurally deferred until an isolated provider exists.

- **Branch strategy is a value union, not a Protocol.** `HeadStrategy` |
  `MergeToHeadStrategy` (frozen dataclasses), passed as `branch_strategy=` to
  `run()` (default `HeadStrategy()` → today's behaviour byte-for-byte). Protocols
  stay reserved for *user-pluggable* behaviour (ADR 0002/0003); a branch strategy
  is a closed set of modes with shared git logic the user never reimplements. A
  new `worktrees.py` holds that logic. See ADR 0007.
- **Host-side strategy execution bracketing the sandbox lifetime.** Worktree git
  (`git worktree add`, the merge-back, `git branch -D`) cannot run through the
  `sandbox.exec` seam — the worktree must exist *before* `sandbox.create()` and the
  merge happens *after* `close()`. The strategy exposes `prepare(cwd)` (host-side,
  pre-create: resolve target branch + create worktree, return the work dir +
  source/target branches + pre-run HEAD) and `finalize(success)` (host-side,
  post-close: merge back, collect commits, decide preservation). `head` is the
  trivial impl (work dir = `cwd`, no worktree, no merge). This is the orchestrator
  rewiring every future strategy depends on. See ADR 0007.
- **`no_sandbox` only.** The bullet proves the seam + worktree lifecycle +
  orchestrator rewiring on the host path. On `no_sandbox` the worktree still
  delivers real value: working-directory isolation, scratch-branch hygiene, and
  the auto-merge-back workflow — all testable without containers. `merge-to-head`
  with a non-`no_sandbox` *library* provider **hard-errors** up front (mirrors the
  agent slice's foreign-flag rejections).
- **Branch semantics.** `branch` (existing prompt arg + `RunResult.branch`) means
  the **target** — where work lands (`head`: the current branch, unchanged;
  `merge-to-head`: the host branch). A new built-in **`source_branch`** (+
  `RunResult.source_branch`) is the branch the agent commits on (`head`: current
  branch; `merge-to-head`: the temp branch). No separate `target_branch` key —
  `branch` already is the target (one name per concept).
- **Worktrees live in-repo at `.pysolated/worktrees/`**, mirroring Sandcastle's
  `.sandcastle/worktrees/`. The bullet auto-writes `.pysolated/worktrees/.gitignore`
  (`*`) so the managed runtime state doesn't surface as untracked noise in the
  git-tracked `.pysolated/` scaffold dir, and so a worktree nested under the main
  tree isn't treated as an untracked nested checkout.
- **Temp branch naming:** `pysolated/<YYYYMMDD-HHMMSS>-<rand>` (random suffix
  avoids second-granularity collisions, per Sandcastle ADR 0018).
- **Failure / preservation contract (data-safety core):**
  - Clean run + merge succeeds → `git merge <temp>` into target, `git branch -D
    <temp>`, remove worktree. `RunResult.commits` = merged SHAs.
  - Merge conflict → `git merge --abort`, **preserve** worktree + temp branch,
    raise a new `MergeConflictError` carrying the worktree path + recovery commands
    (`cd <worktree>` / `git merge <temp>` / `git branch -D <temp>`). No conflict
    markers left in the user's tree.
  - Uncommitted changes left in the worktree on an otherwise-successful run →
    **preserve** the worktree, surface its path, warn. Never delete uncommitted work.
  - `RunResult.preserved_worktree_path: str | None` carries the path in both
    preserve cases.
- **CLI surface:** new `--branch-strategy {head,merge-to-head}` on `run_command`
  (default `head` → today's behaviour). Keeps the CLI == `run()` parity `cli.py`
  asserts. The CLI is `no_sandbox`-only today, so the non-`no_sandbox` hard-error is
  a library-path guard.

## Committed slice scope 2 (tracer bullet: `branch` named strategy + create-or-reuse)

Settled in a grilling session (2026-06-19), the fast-follow after `merge-to-head`.
The bullet proves *a run can place its work on a caller-named branch in a durable
worktree that re-runs reuse*. See ADR 0008.

- **`NamedBranchStrategy(branch=...)`**, third member of the `BranchStrategy`
  union. Runs the agent in a **durable worktree** on the named branch; commits
  stay there; **no merge-back**. source == target == the named branch.
- **CLI:** `--branch-strategy branch` **requires** `--branch <name>`; `--branch`
  with `head`/`merge-to-head` hard-errors (foreign-flag idiom). CLI == `run()`.
- **Durable worktree**, distinct from a preserved worktree (ADR 0008): surfaced as
  a new `RunResult.worktree_path` (always set for `branch`); `preserved_worktree_path`
  stays the merge-to-head exception channel. Never both set; no orchestrator
  strategy-special-casing.
- **Worktree dir** `.pysolated/worktrees/<branch-slashes-as-dashes>` — deterministic
  from the branch name so reuse recomputes + checks existence. Collision caveat
  (`a/b` vs `a-b`) documented; hash-suffix is a later refinement.
- **Create-or-checkout-or-reuse:** existing worktree → reuse (clean → log, dirty →
  warn, never wiped); existing local branch → check out; else → create from `HEAD`.
- **Clear error** when the named branch is already checked out in the main tree
  (git forbids two worktrees on one branch).
- **`no_sandbox`-only**, hard-erroring on other library providers like `merge-to-head`.

## Committed slice scope 3 (tracer bullet: `copy_to_worktree=`)

Settled in a grilling session (2026-06-19), the fast-follow after the `branch`
named strategy. A worktree is a clean `git checkout`, so gitignored host state
(`.env`, `node_modules`, build artifacts) is **absent** inside it — a worktree
run often can't even start the test suite. This bullet closes that gap: it
copies caller-named host paths into the worktree before the agent starts. See
ADR 0009.

- **`run(copy_to_worktree=list[str])`** — a top-level `run()` argument, a list
  of paths read relative to `cwd` and reproduced at the **same relative
  location** inside the worktree. No host→dest remapping (deferred); no absolute
  source paths.
- **Orchestrator-owned, not a strategy hook.** The copy runs after
  `prepare()` (the worktree exists) and before `sandbox.create()`, keyed only on
  "is the work dir a worktree." `prepare`/`finalize` stay the only branch-strategy
  hooks (ADR 0007).
- **`head` hard-errors.** `copy_to_worktree` with `HeadStrategy` raises up front
  (foreign-flag idiom) — `work_dir == cwd`, so there is no worktree to copy into.
  Valid with **both** `merge-to-head` and `branch`.
- **Mechanism:** `cp -a --reflink=auto <src> <dest>` per path — copy-on-write
  (instant on btrfs/xfs, full-copy fallback elsewhere) and **symlink/attribute
  preserving**, which the motivating `node_modules` (pnpm symlink-farm) case
  requires. Each dest parent is `mkdir -p`'d first so nested paths work. **Copy
  timeout deferred** — reflink makes it near-instant on the common path.
- **Fail-fast, validated before `prepare()`** (so a bad path leaves no worktree
  behind): a missing source path errors; a `..`-escaping or absolute source path
  errors (it would write outside the worktree). A worktree run silently missing
  its `.env` produces baffling downstream agent failures — better a crisp
  up-front error.
- **Overwrites on reuse — host wins.** Runs on **every** run, including a `branch`
  reuse of a durable worktree; overwrites whatever is there. A deliberate
  departure from "never wipe uncommitted work" — copied paths are gitignored
  host-owned state, not the agent's tracked work. See ADR 0009.
- **CLI:** repeatable `--copy-to-worktree <path>`, rejected with
  `--branch-strategy head` (foreign-flag idiom), accepted with `merge-to-head`
  and `branch`. CLI == `run()`.
- **`no_sandbox`-only comes free** — `copy_to_worktree` is only reachable via the
  two worktree strategies, which already hard-error off `no_sandbox`. No new
  guard, no new result field.

## Deferred out of the slice

- **`origin` fast-forward refresh on reuse** (Sandcastle ADR 0003's second half)
  and **explicit base ref** (`--base` / `baseBranch`). No remote story in pysolated
  yet; base defaults to `HEAD`. See ADR 0008.
- **Worktree locking** (Sandcastle ADR 0007) — the concurrent-access mitigation,
  sequenced after reuse exactly as Sandcastle did. The gap (two runs sharing one
  durable worktree) is documented. Sequenced **after `copy_to_worktree=`**
  (grilling session 2026-06-19): pysolated has no concurrency story yet, so the
  gap is currently only reachable by a user deliberately running two processes at
  once, whereas `copy_to_worktree` is on the critical path to worktree runs being
  usable at all. Locking returns before container worktree wiring makes concurrent
  runs plausible. See ADR 0008.
- **Standalone `createWorktree()` handle** (own/reuse a worktree across multiple
  `run()` calls) → [entry-points.md](./entry-points.md) territory; the bullet wires
  `branch_strategy=` into `run()` only.
- **Copy timeout + host→dest remapping + absolute source paths** — `copy_to_worktree`
  ships as `cp -a --reflink=auto` of same-relative-location paths (committed slice
  scope 3). A bounding timeout (Sandcastle bounded the copy), a host→worktree dest
  remapping dict, and absolute source paths are each their own follow-up. See ADR 0009.
- **Container (`podman`/`docker`) worktree wiring** — the bind-mount providers need
  a mount-root-vs-exec-cwd split (mount the **repo root** so both `.git` and
  `.git/worktrees/<name>` are visible — a worktree's `.git` is a *file* pointing
  back into the main gitdir — while the agent's cwd is the worktree path), plus
  SELinux `:z` labeling on the worktree path. A coherent self-contained follow-up
  with its own risks; deferred so the bullet stays about the seam, exactly as the
  Codex bullet shipped `no_sandbox`-only.
- **Sync in / sync out + sandbox-owned sync base ref** (Sandcastle ADR 0017) —
  no isolated provider exists to serve them. Returns when one does.
