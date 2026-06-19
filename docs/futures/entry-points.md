# Additional entry points

See [features.md](./features.md) for the index.

- **`interactive()`** — drop into a live, interactive agent session inside the
  sandbox (vs the headless `run()` loop).
- **`createSandbox()`** — a persistent sandbox reused across multiple
  `run()`/`interactive()` calls, with an explicit `close()`.
- The matrix of these scoped to a worktree (`WorktreeRunOptions`,
  `WorktreeInteractiveOptions`, `WorktreeCreateSandboxOptions`).
