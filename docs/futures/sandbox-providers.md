# Sandbox providers (beyond no-sandbox, Podman, Docker)

Remaining sandbox-provider work. The shipped Podman provider lives in
[completed-features.md](./completed-features.md); the active Docker slice has its own
[docker-sandbox-provider.md](./docker-sandbox-provider.md). See
[features.md](./features.md) for the index.

- **Vercel sandbox provider** — remote sandbox; `token`, `ports`, `timeout`,
  `resources`, `runtime`.
- **Daytona sandbox provider** — remote sandbox; `projectId`, `teamId`, `timeoutMs`.
- **Provider categories as first-class concepts** — *bind-mount* providers (host
  filesystem mounted in) vs *isolated* providers (own filesystem, requiring sync).
- **Custom mounts** — `MountConfig` (`hostPath`, `sandboxPath`, `readonly`),
  including git volume mounts on Windows (ADR 0006). Basic `mounts`/`Mount` already
  shipped for Podman/Docker; the remaining work is the Windows git-volume mounts.
- **Sandbox provider env injection** — env contributed by the sandbox provider,
  merged with agent-provider and resolved env at launch.
