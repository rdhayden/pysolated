# Docker UID alignment via build-arg + pre-flight check (no keep-id)

The Docker sandbox provider is a sibling of the Podman provider, but it cannot reuse
Podman's ownership story. Podman aligns host ↔ container file ownership with
`--userns=keep-id:uid=N,gid=N` (ADR 0004): both bind-mounted (host-UID) and
image-built (UID 1000) files appear owned by the same in-container UID at the
namespace level, with no file mutation — which is why the Podman provider can default
`container_uid`/`container_gid` to **1000** independent of the host UID. **Docker has
no `keep-id`.** Its only ownership lever is `--user N:N`, and that only works when the
host UID equals the UID baked into the image; otherwise the agent hits silent `EACCES`
on image-built files (`/home/agent`, the agent CLI). pysolated never used the runtime
`chown` that Sandcastle ADR 0005 removed (it started Podman-first on keep-id), so the
Docker provider goes straight to **build-time UID injection plus a pre-flight check**.

## Decision

- **Build-time:** `pysolated docker build-image` auto-injects `AGENT_UID`/`AGENT_GID`
  build-args defaulted to the host UID/GID (`os.getuid()`/`os.getgid()`, falling back
  to `1000` where unavailable, e.g. Windows), overridable via a repeatable
  `--build-arg KEY=VALUE`. The Containerfile is expected to declare
  `ARG AGENT_UID=1000` / `ARG AGENT_GID=1000`, align its agent user with
  `groupmod -o`/`usermod -o` (the `-o` flag lets alignment succeed when the host
  GID/UID collides with one already in the base image), and end with a **numeric**
  `USER ${AGENT_UID}:${AGENT_GID}` so the pre-flight check can parse it. This slice
  ships the build-arg *plumbing* only — the Containerfile itself is user-provided and
  the contract is documented; shipping templates belongs to the multi-agent
  registry / `init` roadmap items.
- **Runtime:** the `docker()` provider defaults `container_uid`/`container_gid` to the
  host UID/GID (resolved in the factory, like the derived image name) and **always**
  emits `--user N:N` — there is no `userns`-style opt-out, because alignment is a
  single coupled mechanism and disabling it only reintroduces the `EACCES` bug.
- **Pre-flight:** before `docker run`, `create()` runs
  `docker image inspect <image> --format '{{.Config.User}}'`. A failed inspect raises
  `DockerImageNotFoundError`; a numeric `User` that disagrees with the effective
  `container_uid` raises `DockerImageUidMismatchError`, naming both remedies (rebuild
  with `pysolated docker build-image`, or pass `container_uid=<image-uid>`). An image
  with no `USER` directive or a non-numeric `USER` cannot be compared and the check is
  **skipped silently** (documented-contract posture, matching Podman's single-inspect
  preflight). The check is UID-only — `{{.Config.User}}` often omits the GID and a GID
  mismatch rarely causes the `EACCES`-on-binaries failure this guards.

## Considered Options

- **`--userns=keep-id`** — Podman-only, unavailable in Docker. This ADR exists because
  it is not an option.
- **Runtime `chown -R /home/agent`** — rejected for the same reasons Sandcastle ADR
  0005 removed it: startup cost, log spam walking into bind mounts, and failures on
  read-only mounts. pysolated never had it.
- **`fixuid` / entrypoint `/etc/passwd` mutation** — still chowns at startup; solves
  identity but not the performance/log-spam problem.
- **Daemon-level `--userns-remap`** — not per-container, requires Docker daemon
  configuration. Not practical for a library.
- **Default `container_uid` to 1000 like Podman** — rejected: without keep-id the
  `--user` value must match the *host* UID for bind-mounted repo files to be writable,
  so the host UID is the only correct default. The cost is that a `Docker` provider is
  not reproducible across hosts the way `Podman(container_uid=1000)` is — accepted, as
  host-UID alignment is the whole point.
- **Skip the pre-flight** — silent `EACCES` is exactly the failure this prevents; a
  loud, remedy-naming error at create time is worth the one extra `docker image
  inspect`.

## Consequences

- The Docker image contract is heavier than Podman's: it must spell out the
  `ARG`/`groupmod -o`/`usermod -o`/numeric-`USER` pattern, because `--user`, the
  build-args, and the pre-flight all depend on it. This lives in the `Docker` provider
  docstring.
- Images built without `AGENT_UID`/`AGENT_GID` still build (the ARGs default to 1000)
  and still run when the host UID is 1000; the pre-flight catches the non-1000 case and
  names the fix.
- Images using a non-numeric `USER` (e.g. `USER agent`) or no `USER` skip the check and
  may `EACCES` at runtime anyway. A discoverability warning is deferred (the provider is
  display-less); the gap is logged in `docs/futures/features.md`.
- A reader comparing the two providers will find Docker and Podman diverge on UID
  defaults (host vs 1000), user flags (`--user` always vs `--user`+`--userns`, with a
  Podman opt-out and no Docker opt-out), and preflight depth (UID-match vs existence).
  This is intentional and traces entirely to `keep-id` being Podman-only.
