# The `branch` strategy uses a durable worktree, reused by design — origin-refresh and locking deferred

The `branch` **branch strategy** runs the agent in a **worktree** checked out on
a caller-named branch (`NamedBranchStrategy(branch=...)`, CLI `--branch`), leaves
the commits there, and does **not** merge back. Source branch == target branch ==
the named branch.

That worktree is a **durable worktree**: kept on disk *by design* and reused on a
later run targeting the same branch. This is the inverse of `merge-to-head`'s
ephemeral worktree (created fresh, deleted on a clean merge, kept only as an
exception). So the two are surfaced through **distinct result fields** —
`RunResult.worktree_path` (durable, `branch` only) and
`RunResult.preserved_worktree_path` (exceptional recover-here, `merge-to-head`
only); they are never both set, and the orchestrator special-cases neither (it
copies whatever each strategy's `finalize` returns).

Worktree location is `.pysolated/worktrees/<branch-with-slashes-as-dashes>` — a
deterministic function of the branch name, so reuse just recomputes the path and
checks existence. Create-or-checkout-or-reuse, in order: existing worktree →
reuse (clean → log, dirty → warn, never wiped); existing local branch → check it
out; otherwise → create the branch from the host's current `HEAD`.

## Deliberate divergences from Sandcastle (so a reader comparing the two isn't surprised)

- **No `origin` fast-forward refresh** (Sandcastle ADR 0003's second half).
  pysolated has no remote-interaction concept anywhere yet — all three providers
  are bind-mount/local and nothing pushes to `origin`. A network fetch in the
  first `branch` slice would be premature; it returns when there's a remote story
  to serve (alongside the deferred sync-in/sync-out).
- **No worktree locking** (Sandcastle ADR 0007). Sandcastle itself sequenced
  locking *after* reuse as a follow-up for the concurrent-access hole reuse opens.
  We do the same: ship create-or-reuse now, document the concurrent-access gap,
  add locking next.
- **Base ref is always `HEAD`** — no explicit `base_branch` / `--base` yet
  (Sandcastle's `baseBranch`). Deferred; documented.

## Consequences

- `RunResult` carries two worktree-path fields with non-overlapping meanings.
- Two concurrent runs targeting the same named branch share one worktree
  unguarded until locking lands — a documented gap.
- `branch` is `no_sandbox`-only, hard-erroring on other library providers exactly
  like `merge-to-head` (ADR 0007); container worktree wiring lifts the guard for
  both at once, later.
