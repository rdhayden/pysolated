# Sandbox providers are factories; sandboxes are live handles

Adding the Podman provider forced the `SandboxProvider` seam apart. A container is
*long-lived* across a run — `podman run -d … sleep infinity` once, many `podman exec`
calls, `podman rm -f` at the end — but pysolated's original seam had no lifecycle:
`NoSandbox` was simultaneously the frozen config you build with `no_sandbox()` and
the stateless executor (`exec` directly on it). We split the seam into a **factory**
(`SandboxProvider`, holding configuration, one method `create(work_dir) -> Sandbox`)
and a **live handle** (`Sandbox`, owning the running container, with `exec` and
`close`). The orchestrator calls `create()` once before its first exec and `close()`
in a `finally` covering every exit path. `no_sandbox` splits the same way: the
factory's `create()` returns a trivial handle wrapping today's host-subprocess `exec`.

## Considered Options

- **Ephemeral per-exec (`podman run --rm` per call).** Fits the original stateless
  seam with *no* protocol change; the bind-mounted repo carries git state between the
  five execs an iteration makes. Rejected: pays a container start per exec and loses
  any in-container state outside the mount — the wrong default for a long agent run.
- **Stateful provider (`start`/`close` on the provider, container id mutated in).**
  Smaller orchestrator diff, but the provider can no longer be `frozen=True` and one
  instance passed to two concurrent `run()`s corrupts state. Rejected as a latent
  footgun, exactly against the concurrent fan-out the futures backlog points at.

## Consequences

- `core.py` gains a second Protocol; the orchestrator threads a handle (not the
  provider) through `_current_branch`/`_head_sha`/`_commits_since`/the prompt
  executor, and wraps the run body in `try/finally` to `close()`.
- The public `run(sandbox=…)` argument is still the *provider*; internally
  `provider.create(work_dir)` yields the handle the helpers use.
- Teardown is `podman rm -f` from the `finally`, with a per-container `atexit`
  backstop. Abort kills the `podman exec` client and relies on that `rm -f` as the
  true kill switch (a global signal-handler registry was deferred as too invasive
  for a library).
- Providers stay frozen, reusable, and concurrency-safe — each `create()` yields an
  independent sandbox.
