# Docker sandbox provider

**Status: designed, not yet implemented (active slice).** A second bind-mount
container provider, sibling to the shipped Podman provider. The design below was
settled in a grilling session; the central decision (UID alignment) is captured in
[ADR 0005](../adr/0005-docker-uid-alignment-via-build-arg.md). See
[features.md](./features.md) for where this sits in the roadmap.

The one-line *why*: Docker has no Podman `keep-id`, so host ↔ container file
ownership has to be aligned at image **build time** via build-args instead of at the
namespace level. That build-arg alignment + a pre-flight UID check is the defining
work; everything else is inherited from Podman.

---

## Committed slice scope

- **Package layout** — introduce a `sandboxes/` package: shared leaf helpers
  (`_streaming.py`, `_mounts.py`, `_images.py`), plus `no_sandbox.py`, `podman.py`,
  `docker.py`. `__init__.py` re-exports the public + test-facing names so
  `from pysolated.sandboxes import …` and `pysolated/__init__.py` stay unchanged.
  Podman code *relocates* without logic change; `test_podman.py` import paths update
  to the new helper modules.
- **Sibling, no shared base** — `Docker`/`DockerHandle`/`docker()` mirror the Podman
  shapes by deliberate duplication, sharing only the already-module-level leaf
  helpers (`_stream_subprocess`, `Mount`, `_resolve_mount`, `_build_volume_spec`,
  `_derive_default_image_name`). Each provider is read whole so the UID handling can
  be compared side-by-side. (Package split + sibling chosen over a shared base; a
  base is the right move only once a 3rd+ container/remote provider lands.)
- **UID alignment — runtime:** `docker()` defaults `container_uid`/`container_gid`
  to the **host** UID/GID (resolved in the factory, like the derived image name;
  Windows-safe fallback to `1000` where `os.getuid`/`os.getgid` are unavailable).
  `--user N:N` is **always** emitted — no `userns` field, no opt-out (alignment is a
  single coupled mechanism; an opt-out only reintroduces the `EACCES` it prevents).
- **UID alignment — pre-flight:** before `docker run`, `create()` runs
  `docker image inspect <image> --format '{{.Config.User}}'`. Inspect failure →
  `DockerImageNotFoundError`; numeric `User` ≠ effective `container_uid` →
  `DockerImageUidMismatchError`, naming both remedies (rebuild via `pysolated docker
  build-image`, or pass `container_uid=<image-uid>`). No `USER` directive / a
  non-numeric `USER` → **skipped silently** (documented-contract posture, matching
  Podman's single-inspect preflight). UID-only — `{{.Config.User}}` often omits GID
  and a GID mismatch rarely causes the `EACCES`-on-binaries failure this guards.
- **UID alignment — build:** `pysolated docker build-image` auto-injects
  `AGENT_UID`/`AGENT_GID` build-args defaulted to host UID/GID, overridable via a
  repeatable `--build-arg KEY=VALUE` (explicit wins). Host-UID resolution lives in
  the **CLI layer**; the `build_image()` helper is a thin `docker build --build-arg …`
  pass-through taking an explicit `build_args: dict`. **No Containerfile is shipped**
  — plumbing runs against a user-provided Containerfile and the contract is
  documented (see below). `pysolated docker` Typer sub-app mirrors `pysolated podman`
  (`build-image` / `remove-image`).
- **`DockerLaunchError`** for a failed `docker run` (mirrors `PodmanLaunchError`).

## The documented image contract (Docker)

Heavier than Podman's, because `--user`, the build-args, and the pre-flight all
depend on it. The user-provided Containerfile must:

```dockerfile
ARG AGENT_UID=1000
ARG AGENT_GID=1000
RUN groupmod -o -g $AGENT_GID <user> && \
    usermod -o -u $AGENT_UID -g $AGENT_GID -d /home/agent -m -l agent <user>
USER ${AGENT_UID}:${AGENT_GID}
```

`-o` lets alignment succeed when the host UID/GID collides with one already in the
base image; the numeric `USER` is what makes the pre-flight `{{.Config.User}}` check
parseable. `git` + the agent CLI on `PATH`, writable `HOME=/home/agent`.

## Straight inherits from Podman

Same-path mount + `-w cwd` (ADR 0004); argv-passthrough `exec`, no `sh -c`
(ADR 0001); `docker rm -f` close with the 10s timeout + idempotency + the
orchestrator's generic `atexit` backstop; `-e HOME=/home/agent` + provider `env`
(provider wins), **no `os.environ` forward**; `selinux_label="z"` via the shared
volume-spec builder; `mounts` / `cpus` via the shared helpers.

## Deferred out of the slice

- **Shipped Containerfile / agent templates** — belongs to the multi-agent registry
  / `init` scaffolding work ([agent-providers.md](./agent-providers.md) /
  [init-scaffolding.md](./init-scaffolding.md)).
- **Warn on unverifiable image UID** — the pre-flight silently skips images with no
  `USER` / a non-numeric `USER`. A discoverability warning is deferred: the provider
  is display-less, so a warning would introduce a new provider-layer pattern
  (`warnings.warn`/logging) for an edge case.
- **Memory limits** — the slice ships `cpus` only. `--memory` and other resource
  knobs are each their own feature.
- **Extra `docker run` knobs** — `network` (`--network`), `groups` (`--group-add`,
  e.g. docker-outside-of-docker), `devices` (`--device`, e.g. `/dev/kvm`). Same
  deferral as the Podman provider.
- **Intentionally NOT ported: `maxOutputTailChars`/`BoundedTail`** — same reasoning
  as Podman (Python `str` has no V8 max-string cap; a tail risks truncating the
  usage/structured-output bytes the orchestrator parses).
- **Full Windows support** — host-UID resolution falls back to `1000` where
  `os.getuid`/`os.getgid` are unavailable so construction/`build-image` don't crash,
  but real Windows support (mount normalization / gitdir remapping, Sandcastle
  ADR 0006) is deferred. See [platform-correctness.md](./platform-correctness.md).
