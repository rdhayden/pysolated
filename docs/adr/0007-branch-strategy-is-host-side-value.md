# Branch strategy is a host-side value, not a sandbox-routed Protocol

A **branch strategy** decides how a run's git work is placed (`head` — commit
directly on the current branch; `merge-to-head` — commit on a temporary scratch
branch in a worktree, then merge back). We model it as a frozen dataclass union
(`HeadStrategy | MergeToHeadStrategy`) passed as `branch_strategy=` to `run()`,
with the shared git logic in `worktrees.py` — **not** as another injected
`Protocol` seam, and its git runs **on the host**, bypassing `sandbox.exec`.

Two deliberate deviations from established patterns, worth recording because a
reader steeped in ADR 0002/0003 (every seam is a user-injected Protocol) and in
the orchestrator's habit of routing *all* git through the sandbox seam will
reasonably wonder why this one is different:

- **Value union, not a Protocol.** Protocols (`AgentProvider`, `SandboxProvider`,
  `Display`) exist for behaviour a *user* plugs in. A branch strategy is a closed
  set of modes with shared, pysolated-owned git logic the user never reimplements;
  a Protocol would invite an extension point that should not exist and add no
  power. The cost: adding a strategy is a library change, not a user injection —
  which is correct, because strategies *are* library concerns.

- **Host-side execution bracketing the sandbox lifetime.** Worktree git
  (`git worktree add`, the merge-back, `git branch -D`) cannot flow through
  `sandbox.exec`: the worktree must exist *before* `sandbox.create()` (so the agent
  can run in it) and the merge-back happens *after* `close()`. So the strategy
  exposes `prepare(cwd)` (pre-create) and `finalize(success)` (post-close), both
  running on the host. `head` is the trivial impl (work dir = `cwd`, no worktree,
  no merge). The alternative — routing worktree setup through the sandbox seam —
  is impossible given that ordering, and would also wrongly couple host git state
  to the sandbox boundary.

## Consequences

- The orchestrator gains a strategy-driven `prepare`/`finalize` bracket around the
  sandbox lifecycle; the work dir the iterations run in is whatever `prepare`
  returns (the worktree path for `merge-to-head`, `cwd` for `head`).
- Because the git runs host-side, the first cut is `no_sandbox`-only. Container
  providers (`podman`/`docker`) need a separate mount-root-vs-exec-cwd wiring slice
  before `merge-to-head` works there; `merge-to-head` with a non-`no_sandbox`
  provider hard-errors until then.
