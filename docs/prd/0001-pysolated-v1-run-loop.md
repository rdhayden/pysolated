# PRD: pysolated v1 — orchestrate Claude Code via `run()`

## Problem Statement

I want to drive an AI coding agent (Claude Code) over a codebase from Python —
both from my own scripts and from the terminal — and get back a structured result:
what the agent said, what it committed, and (optionally) a validated payload it
produced. Today the only mature tool for this is **Sandcastle**, which is
TypeScript and built on the Effect runtime. There is no Python-native equivalent I
can `import` into a Python project or invoke as a Python CLI.

## Solution

**pysolated** — a Python reimagining of Sandcastle's core. It exposes one primary
capability, **`run()`**, available identically as a library call (`await run(...)`)
and as a Typer CLI (`pysolated run ...`). A **run** drives an **agent** through one
or more **iterations** against a single **prompt**, streams the agent's output to a
**display**, stops early when the agent emits its **completion signal**, and returns
a result carrying the combined output, the **commits** the agent made, and any
**structured output**.

v1 targets a single **agent provider** (`claude_code`) inside a single **sandbox
provider** (`no_sandbox` — the agent runs directly on the **host**, no container).
Real isolation (Docker) is the next slice, not part of v1. The provider seams are
built as injectable abstractions from day one so adding agents/sandboxes later is
purely additive.

pysolated is a *reimagining*, not a line-by-line port: it inherits Sandcastle's
conceptual model and vocabulary but uses idiomatic Python (`asyncio`, `Protocol`s,
exceptions, `snake_case`). See `CONTEXT.md` for the glossary and `docs/adr/` for
the architectural decisions.

## User Stories

1. As a Python developer, I want to call `await run(agent=claude_code(...), sandbox=no_sandbox(), prompt="...")`, so that I can drive a coding agent from my own script.
2. As a CLI user, I want to run `pysolated run --prompt "..."`, so that I can drive the agent from the terminal without writing Python.
3. As a developer, I want `run()` and the CLI to share the same engine, so that behavior is identical whichever interface I use.
4. As a developer, I want to point the agent at a prompt file with `--prompt-file`, so that I can keep long prompts under version control.
5. As a developer, I want to pass an inline prompt string and have it sent to the agent verbatim, so that dynamically-built prompts are never silently rewritten.
6. As a developer, I want `{{KEY}}` placeholders in a prompt file substituted from arguments I provide, so that I can reuse one template with different inputs.
7. As a developer, I want pysolated to inject built-in arguments (e.g. the current branch) into prompt templates, so that I don't have to wire common values myself.
8. As a developer, I want pysolated to reject a run where I pass arguments alongside an inline prompt, so that I find out immediately my arguments would be ignored.
9. As a developer, I want pysolated to reject a run where one of my arguments would override a built-in argument, so that I don't silently shadow framework values.
10. As a developer, I want `` !`command` `` shell expressions in a prompt file evaluated and replaced with their output before the agent runs, so that I can inject live context (e.g. `` !`git log -5` ``) into the prompt.
11. As a developer, I want prompt expansion to fail the run immediately if a shell expression exits non-zero, so that a broken command never produces a silently-truncated prompt.
12. As a developer, I want shell expansion skipped entirely for inline prompts, so that a literal `` !`...` `` in a dynamic prompt is never executed.
13. As a developer, I want the run to execute up to `max_iterations` agent invocations (default 1), so that I can let the agent take multiple passes when needed.
14. As a developer, I want the run to stop as soon as the agent emits the completion signal, so that I don't pay for iterations after the work is done.
15. As a developer, I want to configure the completion signal (string or list of strings), so that I can match whatever marker my prompt instructs the agent to emit.
16. As a developer, I want the agent's text and tool calls streamed live to my terminal, so that I can watch progress in real time.
17. As a developer, I want a Rich terminal display with status messages for iteration start, agent start/stop, and completion, so that the run is legible at a glance.
18. As a developer, I want to choose file-logging instead, writing progress and agent output to a log file, so that I can run unattended and `tail -f` the log.
19. As a developer, I want the run to fail with a clear idle-timeout error if the agent produces no output for too long, so that a stuck agent doesn't hang forever.
20. As a developer, I want a periodic "agent idle for N minutes" warning while waiting, so that I can tell a slow agent from a dead one.
21. As a developer, I want the idle timeout configurable in seconds, so that I can accommodate long-running tools.
22. As a developer, I want a separate completion-grace window that starts once the completion signal is seen, so that an agent whose process lingers (a child holding stdout open) still completes successfully.
23. As a developer, I want the completion grace window to reset on every further output line, so that trailing data emitted after the signal (usage events, result events, output tags) is still captured.
24. As a developer, I want the run to succeed-with-warning (not fail) when the completion grace window expires, so that a hanging-but-done agent is treated as success.
25. As a developer, I want the completion timeout configurable in seconds, so that I can tune the grace window.
26. As a developer, I want the result to include the combined stdout across all iterations, so that I can inspect everything the agent said.
27. As a developer, I want the result to list the commits the agent made during the run by SHA, so that I can see exactly what changed.
28. As a developer, I want only the agent's new commits reported (not pre-existing history), so that the commit list reflects this run alone.
29. As a developer, I want the result to tell me whether a completion signal fired and which one, so that I can branch on whether the agent declared itself done.
30. As a developer, I want to request structured output via `Output.object(tag, model)`, so that the agent's JSON payload inside that XML tag is parsed and validated into a Pydantic instance returned on the result.
31. As a developer, I want `Output.string(tag)` for raw string extraction, so that I can pull a non-JSON payload out of a tag.
32. As a developer, I want the run to fail early if my prompt doesn't contain the configured output tag, so that I don't discover a missing instruction only after paying for an agent run.
33. As a developer, I want structured output to require `max_iterations == 1`, so that the payload unambiguously belongs to the single iteration that produced it.
34. As a developer, I want a clear validation error when the agent's payload doesn't match my schema, so that I can correct the prompt or schema.
35. As a developer, I want to cancel a run via an abort signal (`asyncio` cancellation / `Ctrl-C` from the CLI), so that I can stop an agent mid-flight and have its subprocess killed.
36. As a developer, I want to pass a custom `cwd` so the run anchors to a specific repo directory, so that I can run against a project other than my current directory.
37. As a developer, I want pysolated to surface the agent's exit-code failure with the relevant stderr/output tail, so that I can diagnose a crashed agent.
38. As a library author, I want the agent and sandbox to be injected as typed seams, so that I can supply fakes in my own tests or (later) alternative providers.
39. As a contributor, I want to add a new agent provider by implementing the `AgentProvider` protocol, so that supporting Codex/Copilot later is additive and doesn't touch the loop.
40. As a contributor, I want to add a new sandbox provider by implementing the `SandboxProvider` protocol, so that adding Docker later is additive.
41. As a developer, I want to optionally name a run, so that its log file and status lines are identifiable in multi-run workflows.
42. As a developer, I want to select a Claude model and permission mode through `claude_code(...)`, so that I control which model runs and how permissions are handled.

## Implementation Decisions

**Overall shape.** Plain `asyncio` throughout; no Effect-style runtime. Injected
seams are `Protocol` classes; errors are exceptions from a small hierarchy. See
ADR 0002. Library-first engine with a thin Typer CLI over it; both are co-equal.
Python 3.11+ (`asyncio.timeout`, `TaskGroup`). `src/pysolated/` layout, uv-managed.

**Seam: AgentProvider.** A `Protocol` with: `name`; `env`; `build_command(opts) ->
Command` returning an **argv list + optional stdin** (NOT a shell string — ADR
0001); `parse_stream_line(line) -> list[StreamEvent]`; optional
`parse_session_usage(content) -> Usage | None`. The `claude_code(model, ...)`
implementation builds
`["claude", "--print", "--verbose", "--dangerously-skip-permissions",
"--output-format", "stream-json", "--model", <model>, "-p", "-"]` with the prompt
on stdin, honoring an optional permission mode (mutually exclusive with the
skip-permissions flag). Resume/fork flags are out of scope for v1.

**Seam: SandboxProvider.** A `Protocol` with `exec(argv, *, stdin, cwd, on_line)
-> ExecResult` (exit_code, stdout, stderr), invoking `on_line` per stdout line as
it streams. The `no_sandbox()` implementation spawns a host subprocess via
`asyncio.create_subprocess_exec`, runs in the host repo dir (head strategy — the
agent works directly in the real working directory), and kills the subprocess on
cancellation. No worktrees, no branch strategies in v1.

**Seam: Display.** A `Protocol` (`intro`, `summary`, `status(msg, severity)`,
`text`, `tool_call`). Two impls: a Rich terminal display and a file-logging
display. Selected by a `logging` option (`terminal` default for CLI; `file` writes
to a path). The seam is also the test substitution point for the orchestrator.

**Stream parser (pure).** `parse_stream_line` decodes one Claude `stream-json`
JSONL line into zero or more events: `assistant` lines yield `text` events
(concatenated content blocks) and `tool_call` events (allowlisted tools only:
Bash→command, WebSearch→query, WebFetch→url, Agent→description); `result` lines
yield a `result` event; `system/init` lines yield a `session_id` event;
`assistant.message.usage` informs `parse_session_usage`. Non-JSON / unknown lines
yield nothing.

**Agent invocation (async core).** Streams the agent subprocess while racing three
conditions: an **idle timeout** (fails the iteration with an idle error; emits a
periodic idle warning), a **completion timeout** (a grace window that engages once
a completion signal appears in the accumulated output and resolves the iteration
*successfully* on expiry), and an **abort** (cancels and kills the subprocess).
The idle timer resets on every output line pre-signal; the completion timer resets
on every output line post-signal. Completion-signal matching is a pure helper over
the accumulated text. Defaults: idle 600s, completion 60s, signal
`<promise>COMPLETE</promise>`.

**Orchestrator (async).** Loops `1..max_iterations`: for each iteration, resolves
the full prompt (expansion happens here for templates, skipped for inline), calls
the sandbox to exec the agent command, drives the display, runs commit collection,
and accumulates stdout + commits. Returns early with the matched signal when one
fires; otherwise reports max-iterations reached.

**Prompt pipeline.** `resolve_prompt` picks inline vs file source. Templates go
through **argument substitution** (`{{KEY}}`, with built-in args merged under
user args and a guard rejecting user override of built-ins) then **expansion**
(`` !`cmd` `` evaluated via the sandbox seam, failing fast on non-zero exit).
Inline prompts bypass both and reject any provided args. Built-in args for v1: at
minimum the current branch (the precise built-in set can stay small and grow).

**Structured output (pure).** `Output.object(tag, model)` / `Output.string(tag)`
build an output definition. After the (single) iteration, `extract_structured_output`
scans stdout for `<tag>...</tag>`, parses JSON (object mode) and validates against
the Pydantic model, or returns the raw inner string (string mode). `run()` rejects
the call up front if `max_iterations != 1` or the resolved prompt lacks the opening
tag. Validation failure raises a structured-output error.

**Commit collection.** Before the agent runs, record `HEAD`. After, return
`rev-list <before>..HEAD` as `[{sha}]`. In no-sandbox/head mode commits land
directly on the host's current branch.

**run() (public API).** Keyword-only arguments mirroring the user stories
(`agent`, `sandbox`, `prompt`/`prompt_file`, `prompt_args`, `max_iterations`,
`completion_signal`, `idle_timeout_seconds`, `completion_timeout_seconds`,
`logging`, `name`, `cwd`, `output`, `signal`). Returns a frozen `RunResult`
(`iterations`, `completion_signal`, `stdout`, `commits`, `branch`, `output?`,
`log_file_path?`). Validates option combinations (e.g. output ⇒ single iteration;
inline ⇒ no args) before doing any work.

**CLI.** Typer command `pysolated run` maps flags onto `run()`; constructs the
`claude_code` agent and `no_sandbox` sandbox; defaults to the terminal display;
maps `Ctrl-C` to abort.

## Testing Decisions

A good test asserts **external behavior through the module's public interface**,
not its internals: given inputs (or a sequence of fake-seam outputs), assert the
returned value, raised error, or observable display/seam calls. Avoid asserting on
private helpers, call ordering that isn't contractual, or wall-clock timing —
inject timeouts/clocks so timer behavior is deterministic. Prior art: the
Sandcastle suite (`Orchestrator.test.ts`, `extractStructuredOutput.test.ts`,
`PromptArgumentSubstitution.test.ts`, `AgentProvider.test.ts`) demonstrates the
same module boundaries and table-driven style we mirror in pytest.

Modules to test (all four categories confirmed in scope):

- **Pure deep modules** — *stream parser* (table of JSONL lines → expected event
  lists, incl. tool allowlist and malformed lines), *prompt argument substitution*
  (substitution, missing keys, inline-with-args rejection, built-in-override
  rejection), *structured output extraction* (object parse+validate success,
  validation failure, string mode, missing tag), *completion-signal matching*
  (single/list, substring semantics), *session usage parsing* (last-assistant
  usage, absent usage). Fast, exhaustive, no I/O.
- **Async orchestration** — *agent invocation* and *Orchestrator loop* driven by
  fake `SandboxProvider`/`AgentProvider`/`Display` seams: early-exit on signal,
  idle-timeout failure, completion-grace success (signal-then-hang), grace reset on
  trailing lines, abort kills the subprocess, multi-iteration accumulation. Use
  injected/short timeouts for determinism (`pytest-asyncio`).
- **Provider impls** — *claude_code* command building (argv contents, model flag,
  permission-mode vs skip-permissions exclusivity, prompt-on-stdin), *no_sandbox*
  exec against a real host subprocess (`echo`/`git`): streams lines via `on_line`,
  returns exit code/stdout/stderr, kills on cancel.
- **Commit collection** — against a real temporary git repo: returns only commits
  created between the recorded `HEAD` and the post-run `HEAD`; empty when the agent
  commits nothing.

## Out of Scope

- Docker / real isolation, and any isolated/bind-mount sandbox provider. no-sandbox
  only. (This is the *next* slice — note the project name implies isolation that
  v1 does not yet provide.)
- Worktrees and branch strategies (head/merge-to-head/branch). v1 works in the host
  working directory directly.
- `interactive()` (drop-into-a-session mode).
- Session resume and fork (`--resume` / `--fork-session`) and the host
  session-file plumbing.
- Lifecycle hooks (host/sandbox commands).
- Agent providers other than Claude Code (Codex, Copilot, Cursor, OpenCode, pi) and
  the agent-stream-event forwarding callback.
- `init` scaffolding, build-image / remove-image, and any config-directory concept.
- Multi-context docs (CONTEXT-MAP). pysolated is a single context.

## Further Notes

- Vocabulary and concept definitions live in `CONTEXT.md`; honor it in code and
  docs. Architectural decisions are in `docs/adr/` — notably ADR 0001 (agent
  providers return an argv list, not a shell string) and ADR 0002 (plain asyncio
  instead of porting Effect).
- The provider seams are deliberately built up front despite v1 shipping one impl
  each, so Docker and Codex are additive rather than refactors.
- Reference implementation: the TypeScript Sandcastle repo (`../aihero/sandcastle`).
  Useful for behavior parity (stream formats, default timeouts, completion-grace
  semantics — see Sandcastle ADRs 0019/0020/0010), but its Effect/Layer machinery
  has no counterpart here by design.
