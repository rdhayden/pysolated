# Futures — Sandcastle features deferred from pysolated v1

The [v1 PRD](../prd/0001-pysolated-v1-run-loop.md) deliberately scopes pysolated to
a single vertical slice: `run()` driving Claude Code on the `no_sandbox` provider,
with structured output and prompt templating. This document is the backlog of
**user-facing Sandcastle features that v1 does not include**, kept so the v1 scope
stays honest and nothing is lost.

Each item is a feature a *user* would notice — not an internal refactor. References
point at Sandcastle's concepts (`CONTEXT.md`) and ADRs where they capture the
rationale or the tricky edges. The project has since committed to reaching full
parity (see **Committed roadmap** at the end); individual items below remain a menu
of *what*, while the roadmap fixes the *order*.

---

## 1. Real isolation — sandbox providers

The headline gap: v1's `no_sandbox` provides *no* isolation despite the project
name. Everything below is a sandbox provider beyond no-sandbox.

- **Docker sandbox provider** — run the agent in a container; `imageName`, custom
  `mounts`, `cpus`/memory limits, UID alignment via build-arg (Sandcastle ADR 0014),
  removal of chown-based UID alignment (ADR 0005).
- **Podman sandbox provider** — `userns: keep-id`, SELinux-aware mount flags
  (`:z`, `:ro,z`), `mounts`, `cpus`. **Minimal provider shipped (issue #20):
  factory+handle on the new seam, `keep-id` + same-path `:z` repo mount, argv
  passthrough through `podman exec`, `podman rm -f` teardown + `atexit`
  backstop.** Still ahead inside the Podman track: `mounts` + `cpus` (#21),
  `build-image` / `remove-image` + derived default image name (#22). Deferred
  *out of* the provider slice itself:
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
- **Vercel sandbox provider** — remote sandbox; `token`, `ports`, `timeout`,
  `resources`, `runtime`.
- **Daytona sandbox provider** — remote sandbox; `projectId`, `teamId`, `timeoutMs`.
- **Provider categories as first-class concepts** — *bind-mount* providers (host
  filesystem mounted in) vs *isolated* providers (own filesystem, requiring sync).
- **Custom mounts** — `MountConfig` (`hostPath`, `sandboxPath`, `readonly`),
  including git volume mounts on Windows (ADR 0006).
- **Sandbox provider env injection** — env contributed by the sandbox provider,
  merged with agent-provider and resolved env at launch.

## 2. Branching, worktrees & sync

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

## 3. Additional entry points

- **`interactive()`** — drop into a live, interactive agent session inside the
  sandbox (vs the headless `run()` loop).
- **`createSandbox()`** — a persistent sandbox reused across multiple
  `run()`/`interactive()` calls, with an explicit `close()`.
- The matrix of these scoped to a worktree (`WorktreeRunOptions`,
  `WorktreeInteractiveOptions`, `WorktreeCreateSandboxOptions`).

## 4. Additional agent providers

v1 ships `claude_code` only. Sandcastle also supports:

- **Codex** (`codex exec`, `effort` low/medium/high/xhigh, session storage).
- **Copilot** (`copilot -p --output-format json`, `effort`).
- **Cursor** (`agent --print`; prompt passed via argv with a size guard;
  non-resumable).
- **OpenCode** (`--format json` event stream; session storage).
- **pi** (session storage).
- Per-provider **env manifests + env checks** (declared required vars validated
  before the agent starts) and per-provider Dockerfile templates.

## 5. Agent sessions

- **Session capture** — persist the agent's conversation record to the host
  (provider-owned storage, ADR 0012).
- **Session resume** — `RunResult.resume(prompt)` / `--resume`; continue a prior
  session for one iteration (ADR 0011), requiring filesystem-backed sessions
  (ADR 0016).
- **Session fork** — `RunResult.fork(prompt)` / `--fork-session`; branch a session
  into a new id leaving the parent intact (ADR 0018), enabling concurrent fan-out.
- **Resume precheck** — fail fast when the session to resume doesn't exist on the
  host.

## 6. Token usage reporting

- **Surfacing usage to the user** — `Context window: NNNk` display lines and
  per-iteration token usage on the result. (v1 may parse usage internally but does
  not surface it as a user feature; ADR 0005 — raw tokens, no percentage.)

## 7. Init, scaffolding & image lifecycle

- **`init` command** — scaffold the `.sandcastle/` **config directory** in a repo
  (Dockerfile, `prompt.md`, `config.json`, `.env`/`.env.example`).
- **Templates** — Dockerfile/prompt scaffolds with **template arguments** and
  substitution; templates carry no shared code (ADR 0009).
- **`config.json`** — file-based config (`agent`, `maxIterations`, …).
- **Issue tracker selection** — choose a task source during init (GitHub Issues,
  Beads) so the agent can select **tasks** to work on.
- **Triage labels** — canonical label vocabulary and `--create-label`.
- **Image lifecycle commands** — provider-namespaced `build-image` and
  `remove-image` (e.g. `pysolated docker build-image`), with `--build-image` and
  `--install-template-deps` options at init.
- **Package-manager detection** during scaffolding.

## 8. Environment resolution

- **Multi-source env resolution** — repo-root `.env`, config-dir `.env`, then
  `process.env`, resolving only keys declared in a `.env` file; `.env.example`
  scaffolding.

## 9. Lifecycle hooks

- **Host hooks** — `{ command }` run on the host at lifecycle points.
- **Sandbox hooks** — `{ command, sudo? }` run inside the sandbox.

## 10. Observability & logging extras

- **`onAgentStreamEvent` callback** — forward each agent stream event (text /
  tool call, with iteration number + timestamp) to an external observability system
  (log-to-file mode only).
- **Run logs** under `.sandcastle/logs/` with branch/name-derived filenames.

## 11. Lifecycle timeout overrides

- **Per-step timeout overrides** (Sandcastle ADR 0001) — `copyToWorktreeMs`,
  `gitSetupMs`, `commitCollectionMs`, `mergeToHostMs`. Most cover worktree/merge/sync
  steps that don't exist in v1; they return alongside those features.

## 12. Platform & correctness edges

- **Windows path handling** for worktrees and session stores (ADR 0006).
- **UID/permission alignment** between host and container (ADRs 0005, 0014).

---

## Notes

- This list mirrors Sandcastle as of the reference snapshot in
  `../aihero/sandcastle`. It is a *reimagining* target, so any of these may be
  redesigned (or dropped) rather than ported faithfully when its slice is picked up.

---

## Committed roadmap — full Sandcastle parity, built provider-first

As of 2026-06-17 the project has **committed** to reaching Sandcastle parity (this is
no longer a pure menu). The build order is fixed by the dependency graph — `init`
scaffolding composes everything else, so it lands last:

1. **Podman sandbox provider** (this slice) — factory+handle protocol, long-lived
   container, `userns: keep-id`, same-path bind mount, SELinux flags, `mounts`, `cpus`.
2. **`build-image` / `remove-image` + derived default image name** — needs (1).
3. **Multi-agent registry** (§4) — init picks an agent + writes its Containerfile.
4. **Worktrees + `copyToWorktree`** (§2) — init templates reference them.
5. **Issue-tracker subsystem** (§7) — the `{{LIST_TASKS_COMMAND}}` substitution.
6. **Orchestration-template surface** — the `main.ts`-equivalent pysolated lacks.
7. **`init` scaffolding** (§7) — composes 2–6.

The earlier "next slice is Docker" note is superseded: pysolated goes **Podman-first**
(rootless + keep-id is the stronger isolation story and avoids build-arg UID alignment).
