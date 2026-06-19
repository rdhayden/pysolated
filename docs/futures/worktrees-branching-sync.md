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
