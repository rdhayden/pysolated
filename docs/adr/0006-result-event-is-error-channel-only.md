# `ResultEvent` is an in-band error channel, not a second source of agent text

Adding the Codex agent provider forced a `StreamEvent` the union didn't have.
Sandcastle's `result` event is **overloaded**: it carries both an agent's final
message ("last result wins" → the returned text) *and* in-band errors, because its
orchestrator surfaces `resultText` separately. pysolated is built differently —
`RunResult.text` is the accumulated `TextEvent` prose, and both completion-signal
matching and structured-output extraction run against that prose. Copying the
overload would create two competing sources of "what the agent said." So pysolated
adds `ResultEvent(text: str)` but gives it a deliberately **narrow** role: the
terminal status/error line an agent reports in-band, and nothing else.

## Decision

- Agent providers emit `ResultEvent` **only** for terminal `result`/`error` JSON
  lines carrying a user-facing status/error message (Codex `{type:"error"}` now;
  OpenCode/Copilot/pi terminal+error lines later). Normal assistant prose — including
  a non-streaming agent's one-shot final message, e.g. Codex's `agent_message` —
  always stays `TextEvent`.
- The orchestrator tracks the **last** `ResultEvent` and uses it for exactly one
  purpose: the **stderr-empty fallback** — on a non-zero exit where `stderr` is
  empty, its text becomes the `AgentExecutionError` message. It is also surfaced live
  via the existing `display.status(..., "error")`.
- `ResultEvent` does **not** feed `prose`, completion-signal matching,
  structured-output extraction, or `RunResult`. `TextEvent` remains the single source
  of "what the agent said," so the Claude path is byte-for-byte unchanged.

## Why it's needed

Codex emits auth-failure / rate-limit / API errors as JSON on **stdout** (the
process may even exit 0). Without an error channel, pysolated's `AgentExecutionError`
would surface an empty `stderr` and the user would get a useless error. The event is
added now, rather than per-agent later, because all four remaining agents (Codex,
OpenCode, Copilot, pi) emit terminal `result`/`error` lines — adding it once avoids
reshaping the union and the orchestrator four more times.

## Considered Options

- **Copy Sandcastle's overloaded `result`** (final text + errors) — rejected: it
  duplicates the "agent text" source pysolated already owns via prose, and would make
  completion-signal/structured-output extraction ambiguous about which source wins.
- **Name it `ErrorEvent`** — honest about its only current use, but `ResultEvent`
  keeps the stream-json `type` vocabulary and leaves room to carry non-error terminal
  text later without renaming.
- **Expose the last `ResultEvent` on `RunResult`** (e.g. `RunResult.result`) —
  deferred: it partly overlaps `text` and no caller needs it yet.
- **No new event; route Codex errors through `TextEvent`** — rejected (this slice's
  first instinct): it pollutes prose, so completion-signal matching and
  structured-output extraction would see error text as agent output.

## Consequences

- A reader cross-referencing Sandcastle will find pysolated's `ResultEvent`
  intentionally *thinner* than Sandcastle's `result`; this ADR is why.
- A non-streaming agent's final message reaches `prose` via `TextEvent`, so it
  displays in one flush at end-of-turn rather than trickling (documented in
  `docs/futures/agent-providers.md`). Tool-call events still stream live, so a run is
  never silent.
- No new `Display` method — the error surfaces through the existing
  `status(..., "error")` seam.
