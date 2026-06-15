# pysolated

A Python toolkit that orchestrates AI coding agents, managing the lifecycle of
sandboxes, prompts, and iterations. A Python reimagining of the TypeScript tool
[Sandcastle](../aihero/sandcastle); it inherits Sandcastle's conceptual model but
adopts Python conventions (`snake_case`, asyncio) and is free to diverge.

## Language

### Core concepts

**pysolated**:
The Python toolkit (library + CLI) that orchestrates an **agent** inside a **sandbox**.
_Avoid_: "Sandcastle" (the TypeScript original), "the tool", "the CLI".

**Sandbox**:
The isolation boundary around the **agent** -- a container, VM, or similar
environment that constrains the **agent**'s access.
_Avoid_: "container" (too specific), "workspace".

**Host**:
The developer's machine where pysolated runs and the real git repo lives.
_Avoid_: "local" (ambiguous -- the sandbox also has a local filesystem).

**Agent**:
The AI coding tool invoked inside the **sandbox** (e.g. Claude Code, Codex).
_Avoid_: "the bot", "Claude" (too specific -- the agent is swappable).

### Providers

**Agent provider**:
A pluggable implementation that builds commands and parses output for a specific
**agent**, passed to `run()` via the `agent` argument.
_Avoid_: "agent adapter", "agent driver".

**Sandbox provider**:
A pluggable implementation that creates and manages a **sandbox**, passed to
`run()` via the `sandbox` argument.
_Avoid_: "backend", "runtime".

**No-sandbox provider**:
A **sandbox provider** where no container is created -- the **agent** runs
directly on the **host**. The starting point for pysolated; note it provides no
actual isolation despite the project name.
_Avoid_: "local provider", "host provider".

### Execution

**Run**:
The public entry point (`run()`) that drives an **agent** through one or more
**iterations** against a single **prompt** and returns a result. Exists as both a
library call and a CLI command.
_Avoid_: "session" (reserved for **agent session**), "job".

**Iteration**:
A single invocation of the **agent** inside the **sandbox**, producing at most one
commit.
_Avoid_: "cycle", "loop", "run" (reserved for the `run()` entry point).

**Prompt**:
The instruction text passed to the **agent** at the start of each **iteration**.
_Avoid_: "system prompt" (too specific), "message".

**Completion signal**:
A marker (default `<promise>COMPLETE</promise>`) in the **agent**'s output that
indicates all actionable work is finished and stops the **iteration** loop early.
A pure termination signal -- carries no payload.
_Avoid_: "done flag", "exit signal".

**Idle timeout**:
A silence-based timer that fails the **run** if the **agent** produces no output
for the configured duration.
_Avoid_: "stall timeout".

**Completion timeout**:
A grace window that takes over once a **completion signal** is seen in the
**agent**'s output. If the **agent** process has not exited when it expires, the
**iteration** force-completes *successfully* with a warning (the process is
hanging, not stuck). Reset by every subsequent output line so trailing data is
still captured. Distinct from **idle timeout**, which fails the run.
_Avoid_: "grace period" (too generic), "drain timeout".

### Prompts

**Inline prompt**:
A **prompt** passed directly as a string. Sent to the **agent** as-is -- no
**prompt argument** substitution, no **prompt expansion**.
_Avoid_: "string prompt", "dynamic prompt".

**Prompt template**:
A **prompt** sourced from a file. May contain `{{KEY}}` placeholders and
`` !`command` `` **shell expressions**, resolved before the **prompt** reaches the
**agent**.
_Avoid_: "prompt file" (that's the input, not the concept).

**Prompt argument**:
A value that substitutes a `{{KEY}}` placeholder in a **prompt template**. Some are
**built-in** (injected by pysolated); the rest are supplied by the caller.
_Avoid_: "template variable", "parameter".

**Prompt expansion**:
The preprocessing step that evaluates **shell expressions** in a **prompt
template**, replacing each with its stdout.
_Avoid_: "command expansion".

**Shell expression**:
A `` !`command` `` marker in a **prompt template** that runs a shell command and is
replaced by its stdout during **prompt expansion**.
_Avoid_: "inline command".

### Structured output

**Structured output**:
A schema-validated payload the **agent** emits inside a caller-specified XML tag,
parsed out of the **agent**'s output and returned from `run()`. Orthogonal to the
**completion signal** -- a **run** may use either, both, or neither.
_Avoid_: "result", "JSON output".

**Output schema**:
The Pydantic model the caller passes alongside the tag name to parse and validate
**structured output**.
_Avoid_: "validator", "result schema".

## Example dialogue

> **Dev:** When I call `run()` with the no-sandbox provider, where does the agent
> actually execute?
>
> **Maintainer:** Directly on the host -- no-sandbox creates no sandbox, so there's
> no isolation boundary. The agent touches your real working directory.
>
> **Dev:** And one `run()` can do several iterations?
>
> **Maintainer:** Right. Each iteration is one agent invocation. The loop stops
> early the moment the completion signal shows up in the agent's output -- otherwise
> it runs until it hits max iterations.
>
> **Dev:** What if the agent emits the signal but the process just sits there?
>
> **Maintainer:** That's what the completion timeout is for. Once we've seen the
> signal, the idle timeout hands off to the completion timeout; when that grace
> window expires we force-complete *successfully* with a warning. The idle timeout
> only fires before any signal, and that one fails the run.
