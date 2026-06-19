# Completed features

Shipped slices, moved out of the active backlog so the per-feature files in this
directory stay a menu of *remaining* work. Each entry keeps its deferrals so the
record of what was consciously left out isn't lost. See [features.md](./features.md)
for the index and roadmap.

---

## Podman sandbox provider — SHIPPED (issues #20, #21, #22)

`userns: keep-id`, SELinux-aware mount flags (`:z`, `:ro,z`), `mounts`, `cpus`.
**Minimal provider shipped (issue #20):** factory+handle on the new seam, `keep-id`
+ same-path `:z` repo mount, argv passthrough through `podman exec`, `podman rm -f`
teardown + `atexit` backstop. **`mounts` + `cpus` shipped (#21). `pysolated podman
build-image` / `remove-image` + derived default image name shipped (#22).**

Deferred *out of* the provider slice itself:

- **File-mount parent creation** — Sandcastle's `processFileMountParents`: a root
  `mkdir -p && chown` exec after `podman run` so mounting a single file whose
  sandbox-side parent dir is absent works. v1 passes mounts through as plain `-v`
  and documents "the sandbox-side parent directory must exist in the image".
- **Sandbox-side tilde resolution** — `sandbox_path` is absolute-only in v1; no
  `~`→sandbox-home expansion (pysolated has no `sandboxHomedir` concept).
- **Windows mount normalization** — `normalizeMounts`/gitdir remapping (ADR 0006);
  pysolated is platform-linux and has no worktree/gitdir mounts.
- **Extra `podman run` knobs** — `network` (`--network`), `groups` (`--group-add`,
  e.g. docker-outside-of-docker), `devices` (`--device`, e.g. `/dev/kvm`). v1 ships
  `cpus` only; these are each their own feature with their own justification.
- **Global signal-handler cleanup registry** — Sandcastle's `shutdownRegistry`
  (shared SIGINT/SIGTERM listener `rm -f`-ing every live container). v1 relies on
  the orchestrator's `finally` + a per-container `atexit` backstop; a library
  hijacking process-wide signals is invasive. SIGKILL leaks stay unavoidable.
- **Intentionally NOT ported: `maxOutputTailChars`/`BoundedTail`.** It guards V8's
  max-string-length crash; Python `str` has no such cap, `no_sandbox` already
  accumulates unbounded, and a tail risks truncating the usage/structured-output
  bytes the orchestrator parses. If output-bounding is ever wanted it's a
  cross-provider seam concern, not a Podman option.
- **Active image-contract validation** — v1 documents the image contract (user at
  `container_uid:gid`, `git` + agent CLI on `PATH`, writable `HOME`) and relies on
  a single `podman image inspect` existence preflight. Actively probing the image
  (`exec id`, `which git`, `which claude` at create time) is a later refinement.
- **`podman machine` preflight** — Sandcastle checks a running Podman Machine on
  macOS/Windows before create. v1 is platform-linux so it's skipped; when pysolated
  goes multi-platform this preflight (and the matching clear error) returns.

## Image lifecycle — `build-image` / `remove-image` + derived default image name — SHIPPED (issue #22)

`pysolated podman build-image` / `remove-image`, plus the derived default image name
(`pysolated:<sanitized-host-dirname>`) shared by the provider and the CLI. This was
roadmap item 2 (needs the Podman provider). The provider-namespaced shape
(`pysolated <provider> build-image`) is the template the Docker slice mirrors.
