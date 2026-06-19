# Additional agent providers

Roadmap item 3 (multi-agent registry — init picks an agent + writes its
Containerfile). v1 ships `claude_code` only. See [features.md](./features.md) for the
index.

Sandcastle also supports:

- **Codex** (`codex exec`, `effort` low/medium/high/xhigh, session storage).
- **Copilot** (`copilot -p --output-format json`, `effort`).
- **Cursor** (`agent --print`; prompt passed via argv with a size guard;
  non-resumable).
- **OpenCode** (`--format json` event stream; session storage).
- **pi** (session storage).
- Per-provider **env manifests + env checks** (declared required vars validated
  before the agent starts) and per-provider Dockerfile templates.

---

## Committed slice scope (tracer bullet: registry + Codex)

Settled in a grilling session. The bullet proves *an agent can be swapped at the
registry seam* — the registry plus **one** additional provider (**Codex**, chosen
because it stresses the seam hardest: a distinct `codex exec` verb, stdin-delivered
prompt, its own usage shape, and in-band errors). The other four agents are
fast-follow repetitions of the proven pattern.

- **Package split first.** `agents.py` becomes an `agents/` package in a separate
  no-logic-change commit, mirroring the sandboxes split (issue #24): `__init__.py`
  re-exports the public names; `_parsing.py` holds the shared `TOOL_ARG_FIELDS` +
  assistant-content block parser (Claude & Copilot share the stream-json assistant
  shape); `_registry.py` holds the registry + CLI-builder; `claude_code.py`
  relocates verbatim; `codex.py` is new. `pysolated/__init__.py`'s public surface
  is unchanged.
- **Registry = `dict[str, factory]`** keyed on each provider's `.name`
  (`"claude-code"`, `"codex"`). It is a string-name boundary for the CLI (and later
  init/config) only — library callers import the typed factories directly. It holds
  no per-agent metadata; that grows in later slices that need it.
- **CLI-builder** (`build_agent(name, *, model, effort, permission_mode)`) lives next
  to the registry and applies provider-specific options, so `run_command` grows no
  `if name == …` ladder. Foreign flags **hard-error** (`typer.Exit(2)`): `--effort`
  with `claude-code`, `--permission-mode` with `codex`, unknown `--agent`. Mirrors
  the existing `--prompt/--prompt-arg` rejections.
- **CLI surface:** new `--agent` (default `"claude-code"` → today's behaviour
  byte-for-byte) and `--effort` (validated `low|medium|high|xhigh`). The
  `claude-opus-4-7` default model applies **only** to `claude-code`; every other
  agent **requires `--model`** (omitting it errors up front, rather than smuggling a
  Claude model into Codex or baking a churning per-agent default into the registry).
- **`ResultEvent` added to the `StreamEvent` union.** Codex breaks two assumptions
  the union was built on, so the union gains one variant now (avoiding rework when
  the other four agents land — they all emit terminal `result`/`error` lines):
  - Codex emits its final message as one `agent_message` (not streamed deltas) →
    mapped to a `TextEvent`, so `prose` still holds it; the only loss is live
    trickle (documented; tool-call events still stream).
  - Codex emits auth/rate-limit/API errors as `{type:"error"}` lines on **stdout**
    (process may even exit 0). These map to `ResultEvent`. Its role is deliberately
    **narrow** — unlike Sandcastle's overloaded `result`, pysolated's `ResultEvent`
    does **not** feed `prose`, completion-signal matching, structured-output
    extraction, or `RunResult`. The orchestrator tracks the last one and uses it for
    exactly one thing: the **stderr-empty fallback** in `AgentExecutionError` (plus
    a live `display.status(..., "error")`). `TextEvent` stays the single source of
    "what the agent said," so the Claude path is unchanged. See ADR 0006.
- **Codex invocation (argv, ADR 0001):**
  `["codex", "exec", "--json", "--dangerously-bypass-approvals-and-sandbox", "-m",
  model]`, plus `["-c", 'model_reasoning_effort="<effort>"']` when effort is set,
  with `stdin=prompt`. The bypass flag is **always** emitted, no opt-out — symmetric
  with `claude_code`'s default `--dangerously-skip-permissions`.
- **Codex usage** (`Codex.parse_session_usage`) scans stdout in reverse for the last
  `turn.completed` and maps it to the four-field `Usage`: `input_tokens =
  input − cached`, `cache_read = cached`, `cache_creation = 0`, `output = output`
  (cached is a subset of input; subtracting avoids double-counting). Missing/malformed
  → `None`, same contract as Claude.

## Deferred out of the slice

- **Session storage (capture / resume / fork)** → [agent-sessions.md](./agent-sessions.md).
  New providers ship **session-less** (like Sandcastle's cursor/copilot/opencode).
  Codex's `codex exec resume`/`fork` verbs and `--fork-session` are not built here.
- **Env manifests + env checks** → [env-resolution.md](./env-resolution.md). Codex's
  required auth vars are **not** validated; the `env` dict still passes through.
  Consistent with `claude_code` today (no env check).
- **Shipped Containerfile / Dockerfile templates** → init slice
  ([init-scaffolding.md](./init-scaffolding.md)). The documented contract stands:
  the `codex` CLI must be on `PATH` (on the **host** for the default `no_sandbox`
  run, exactly as `claude` is today).
- **Codex `approvals_reviewer="auto_review"`** (the `-a on-request -s
  danger-full-access` reviewer-as-boundary mode) — a multi-agent-review feature with
  no meaning without the reviewer agent.
- **`effort` on `claude_code`** — pysolated's `claude_code` stays effort-less this
  slice (so `--effort --agent claude-code` errors). Claude's CLI does support it; a
  trivial fast-follow, out of scope here.
- **The other four agents** (Copilot, Cursor, OpenCode, pi) — each a repetition of
  the proven registry pattern, landing after the bullet. Cursor/Copilot add the
  argv-size guard (they take the prompt positionally, not via stdin).
