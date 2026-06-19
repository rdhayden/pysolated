# Platform & correctness edges

See [features.md](./features.md) for the index.

- **Windows path handling** for worktrees and session stores (ADR 0006).
- **UID/permission alignment** between host and container (ADRs 0005, 0014). The
  Docker half of this is the active slice — see
  [docker-sandbox-provider.md](./docker-sandbox-provider.md) and
  [ADR 0005](../adr/0005-docker-uid-alignment-via-build-arg.md).
