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
| Init, scaffolding & image lifecycle (issue tracker, init) | **5, 7** | [init-scaffolding.md](./init-scaffolding.md) |
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
5. **Issue-tracker subsystem** — the `{{LIST_TASKS_COMMAND}}` substitution.
   → [init-scaffolding.md](./init-scaffolding.md)
6. **Orchestration-template surface** — the `main.ts`-equivalent pysolated lacks.
7. **`init` scaffolding** — composes 2–6. → [init-scaffolding.md](./init-scaffolding.md)

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
