# Futures — Sandcastle features deferred from pysolated v1

The [v1 PRD](../prd/0001-pysolated-v1-run-loop.md) deliberately scopes pysolated to
a single vertical slice: `run()` driving Claude Code on the `no_sandbox` provider,
with structured output and prompt templating. This document is the backlog of
**user-facing Sandcastle features that v1 does not include**, kept so the v1 scope
stays honest and nothing is lost.

Each item is a feature a *user* would notice — not an internal refactor. References
point at Sandcastle's concepts (`CONTEXT.md`) and ADRs where they capture the
rationale or the tricky edges. Nothing here is committed-to; it's a menu for future
slices.

---

## 1. Real isolation — sandbox providers

The headline gap: v1's `no_sandbox` provides *no* isolation despite the project
name. Everything below is a sandbox provider beyond no-sandbox.

- **Docker sandbox provider** — run the agent in a container; `imageName`, custom
  `mounts`, `cpus`/memory limits, UID alignment via build-arg (Sandcastle ADR 0014),
  removal of chown-based UID alignment (ADR 0005).
- **Podman sandbox provider** — `userns: keep-id`, SELinux-aware mount flags
  (`:z`, `:ro,z`), `mounts`, `cpus`.
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
- The natural **next slice after v1** is Section 1 (Docker) + the parts of Section 2
  it requires (worktrees, merge-to-head) — that's what makes pysolated live up to
  its name.
