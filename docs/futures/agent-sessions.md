# Agent sessions

See [features.md](./features.md) for the index.

- **Session capture** — persist the agent's conversation record to the host
  (provider-owned storage, ADR 0012).
- **Session resume** — `RunResult.resume(prompt)` / `--resume`; continue a prior
  session for one iteration (ADR 0011), requiring filesystem-backed sessions
  (ADR 0016).
- **Session fork** — `RunResult.fork(prompt)` / `--fork-session`; branch a session
  into a new id leaving the parent intact (ADR 0018), enabling concurrent fan-out.
- **Resume precheck** — fail fast when the session to resume doesn't exist on the
  host.
