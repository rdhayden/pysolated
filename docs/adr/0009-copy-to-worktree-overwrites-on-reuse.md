# `copy_to_worktree` overwrites on reuse — host is the source of truth

`copy_to_worktree=[...]` copies host paths into a run's worktree before the
agent starts (filling the gap that a worktree is a clean `git checkout` —
gitignored host state like `.env` / `node_modules` is absent). It runs on
**every** run, including a `branch` strategy **reuse** of a durable worktree,
and **overwrites** whatever is already there: the host wins.

This deliberately departs from the worktree subsystem's otherwise-loud "never
wipe uncommitted work" principle (ADR 0008, the merge-to-head preservation
contract). The departure is sound because `copy_to_worktree` targets
gitignored, host-owned state — not the agent's tracked, committable work.
Overwriting `.env` with the host's `.env` honours the declared contract ("these
host paths should be present and current in the worktree"); it does not destroy
work. Treating the parameter as declarative (host-authoritative, idempotent
overwrite) also keeps the copy step strategy-agnostic — the orchestrator copies
after `prepare()` whenever the work dir is a worktree, without threading
`reuse_status` into the decision.

## Consequences

- A stale `.env` never lingers in a reused durable worktree; the host refreshes
  it each run.
- A user who hand-edits a copied path *inside* the worktree loses that edit on
  the next run — acceptable, because copied paths are declared host-owned, and
  the durable worktree's purpose is its tracked commits, not its untracked state.
- The copy is orchestrator-owned, not a strategy hook: `prepare`/`finalize`
  remain the only branch-strategy hooks (ADR 0007), and the copy keys only off
  "is the work dir a worktree," not off which strategy produced it.
