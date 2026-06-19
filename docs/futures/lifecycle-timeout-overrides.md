# Lifecycle timeout overrides

See [features.md](./features.md) for the index.

- **Per-step timeout overrides** (Sandcastle ADR 0001) — `copyToWorktreeMs`,
  `gitSetupMs`, `commitCollectionMs`, `mergeToHostMs`. Most cover worktree/merge/sync
  steps that don't exist in v1; they return alongside those features.
