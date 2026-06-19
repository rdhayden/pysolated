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
environment that constrains the **agent**'s access. The *live* object: created by
a **sandbox provider** for a single **run**, it owns the running environment, is
the thing the orchestrator `exec`s commands into, and is torn down when the run
ends. Distinct from the **sandbox provider** that builds it.
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
A pluggable *factory* for **sandboxes**, passed to `run()` via the `sandbox`
argument. The caller builds and configures it once (image, mounts, limits); the
orchestrator asks it to create a fresh **sandbox** per **run**. The provider holds
configuration; the **sandbox** it creates holds the live environment.
_Avoid_: "backend", "runtime".

**Agent registry**:
A name → **agent provider** factory map that resolves a *string* agent name
(from the CLI `--agent` flag, and later from init/config) to a concrete **agent
provider**. It exists only for the string-name boundary -- library callers
construct providers directly via their typed factories and never touch the
registry. The registry holds no behaviour beyond lookup; provider-specific
launch options stay on each factory's typed signature.
_Avoid_: "agent map", "provider table", "plugin registry".

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

### Branching & worktrees

**Branch strategy**:
How a **run**'s git work is placed. `head` runs the **agent** directly on the
current branch (no worktree); `merge-to-head` runs it in a **worktree** on a
temporary scratch branch and merges that back to the current branch when the run
ends; `branch` runs it in a **durable worktree** on a caller-named branch and
leaves the commits there (no merge-back). A *value* passed to `run()` (a closed
set of modes with shared git behaviour), not a user-pluggable **provider**
Protocol.
_Avoid_: "branch mode", "git strategy".

**Worktree**:
A git worktree pysolated creates under `.pysolated/worktrees/` to isolate one
**run**'s working directory from the **host**'s main checkout. Starts as a clean
checkout of the **source branch** — untracked host files are absent until copied
in.
_Avoid_: "checkout", "clone", "scratch dir".

**Durable worktree**:
The **worktree** the `branch` **branch strategy** keeps on disk across **runs**
as the named branch's working directory. Unlike a **preserved worktree** (kept
because something went wrong), a durable worktree persists *by design* — there is
no merge-back, the **agent**'s commits live on the named branch in it, and a
later **run** targeting the same branch reuses it. Its path is surfaced on the
result.
_Avoid_: "persistent worktree", "named worktree", "preserved worktree" (that's
the exception case, not this).

**Source branch**:
The branch the **agent** commits to *during* a **run**. For `head` it is the
current branch; for `merge-to-head` it is the temporary scratch branch the
**worktree** is on; for `branch` it is the caller-named branch. Surfaced as the
`source_branch` **prompt argument** and on the result.
_Avoid_: "work branch", "feature branch", "temp branch" (that's only the
merge-to-head case).

**Target branch**:
The branch the work lands on. For `head` it equals the **source branch**; for
`merge-to-head` it is the **host**'s current branch the scratch branch merges
into; for `branch` it equals the **source branch** (the named branch — no
merge-back). This is what `RunResult.branch` and the `branch` **prompt argument**
report — where commits ended up.
_Avoid_: "base branch", "destination branch".

**Preserved worktree**:
A **worktree** pysolated leaves on disk instead of removing — because a
merge-back conflicted or the worktree held uncommitted changes — so no work is
lost. Its path is surfaced on the result.
_Avoid_: "orphaned worktree", "leftover worktree".

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
>
> **Dev:** If I don't want the agent committing straight onto my current branch?
>
> **Maintainer:** Use the `merge-to-head` branch strategy. We cut a worktree on a
> throwaway scratch branch — that's the source branch — let the agent commit there,
> then merge it back to your current branch, the target, and delete the scratch
> branch. `RunResult.branch` is always the target, so it reads as "where my commits
> ended up."
>
> **Dev:** And if the merge conflicts, or the agent left uncommitted changes?
>
> **Maintainer:** We don't throw anything away. On a conflict we abort the merge and
> leave the worktree and its branch on disk; on a clean merge with leftover
> uncommitted changes we still keep the worktree. Either way it's a preserved
> worktree and its path comes back on the result so you can finish by hand.
