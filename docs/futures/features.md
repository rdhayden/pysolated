# Futures — pysolated backlog index

The [v1 PRD](../prd/0001-pysolated-v1-run-loop.md) scoped pysolated to a single
vertical slice: `run()` driving Claude Code on the `no_sandbox` provider, with
structured output and prompt templating. This directory is the backlog of
**user-facing Sandcastle features beyond v1**, kept so scope stays honest and nothing
is lost. The project has committed to reaching full Sandcastle parity (see the
roadmap below).

**This file is an index.** Each feature lives in its own file so an agent picking up
the next slice reads only that one — not the whole backlog. Shipped work is in
[completed-features.md](./completed-features.md).

## Feature files

| Feature | Roadmap | File |
| --- | --- | --- |
| Docker sandbox provider | active sibling | [docker-sandbox-provider.md](./docker-sandbox-provider.md) |
| Other sandbox providers (Vercel, Daytona, categories, mounts, env) | — | [sandbox-providers.md](./sandbox-providers.md) |
| Additional agent providers (multi-agent registry) | **3** | [agent-providers.md](./agent-providers.md) |
| Branching, worktrees & sync | **4** | [worktrees-branching-sync.md](./worktrees-branching-sync.md) |
| Init, scaffolding, image lifecycle & issue tracker | **5, 7** | [init-scaffolding.md](./init-scaffolding.md) |
| Additional entry points (`interactive()`, `createSandbox()`) | — | [entry-points.md](./entry-points.md) |
| Agent sessions (capture / resume / fork) | — | [agent-sessions.md](./agent-sessions.md) |
| Token usage reporting | — | [token-usage-reporting.md](./token-usage-reporting.md) |
| Environment resolution | — | [env-resolution.md](./env-resolution.md) |
| Lifecycle hooks | — | [lifecycle-hooks.md](./lifecycle-hooks.md) |
| Observability & logging extras | — | [observability-logging.md](./observability-logging.md) |
| Lifecycle timeout overrides | — | [lifecycle-timeout-overrides.md](./lifecycle-timeout-overrides.md) |
| Platform & correctness edges | — | [platform-correctness.md](./platform-correctness.md) |

The "Orchestration-template surface" (roadmap item 6 — the `main.ts`-equivalent
pysolated lacks) has no menu file yet; it composes the items above and lands with the
init work.

## Committed roadmap — full Sandcastle parity, built provider-first

As of 2026-06-17 the project has **committed** to reaching Sandcastle parity. The
build order is fixed by the dependency graph — `init` scaffolding composes everything
else, so it lands last:

1. ✅ **Podman sandbox provider** — done (issues #20, #21). See
   [completed-features.md](./completed-features.md).
2. ✅ **`build-image` / `remove-image` + derived default image name** — done (#22).
   See [completed-features.md](./completed-features.md).
3. **Multi-agent registry** — init picks an agent + writes its Containerfile.
   → [agent-providers.md](./agent-providers.md)
4. **Worktrees + `copyToWorktree`** — init templates reference them.
   → [worktrees-branching-sync.md](./worktrees-branching-sync.md)
5. ~~**Issue-tracker subsystem** — the `{{LIST_TASKS_COMMAND}}` substitution.~~
   **Folded into init (item 7).** The issue-tracker registry only earns its keep
   bundled with its tool-install Dockerfile snippet and `.env.example` lines, all
   written into the scaffold together — that bundling *is* init. pysolated already
   has `{{KEY}}` substitution + `prompt_args` + `` !`command` `` expansion, so the
   command alone needs no standalone subsystem. There is no honest run-loop surface
   for it before init exists. → [init-scaffolding.md](./init-scaffolding.md)
6. ~~**Orchestration-template surface** — the `main.ts`-equivalent pysolated lacks.~~
   **Folded into init (item 7)** (decided 2026-06-23). Sandcastle's `main.ts`
   "orchestration template" is just a single configured `run()` call — the iteration
   loop already lives *inside* `run()` (`max_iterations` + `completion_signal`), which
   pysolated has shipped. So item 6 has **no new library surface** (no `run_loop()` —
   that would re-implement `run()`); it is a scaffolded, user-owned template *artifact*,
   and an artifact only exists to be written by init. Same situation that folded item 5.
   The `while True` + HITL pause in pysolated's dogfood `.pysolated/main.py` is a
   divergence *beyond* Sandcastle's `simple-loop` template (which has neither).
   → [init-scaffolding.md](./init-scaffolding.md)
7. **`init` scaffolding** — composes 2–6 (incl. the issue-tracker registry from 5 and
   the orchestration template from 6). **The next feature.**
   → [init-scaffolding.md](./init-scaffolding.md)

pysolated went **Podman-first** (rootless + keep-id is the stronger isolation story
and avoids build-arg UID alignment). With Podman shipped, the **Docker sandbox
provider** is now being built as a post-Podman *sibling*
([docker-sandbox-provider.md](./docker-sandbox-provider.md)) — it does not displace
roadmap items 3–7, which remain the parity-critical path.

## Notes

- This backlog mirrors Sandcastle as of the reference snapshot in
  `../aihero/sandcastle`. It is a *reimagining* target, so any item may be redesigned
  (or dropped) rather than ported faithfully when its slice is picked up.
- Each feature is something a *user* would notice — not an internal refactor.
  References point at Sandcastle's concepts (`CONTEXT.md`) and ADRs.
