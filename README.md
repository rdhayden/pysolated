# pysolated

A Python toolkit that orchestrates AI coding agents inside sandboxes to enable automated Ralph like loops in sequence or in parralel for example to automate the completion of tickets on a backlog marked as not needing humans in the loop, or to spin up and down isolated agents to check code in continuous integration loops. 

A Python
reimagining of [Sandcastle](https://github.com/ai-hero-dev/sandcastle); see
[`CONTEXT.md`](CONTEXT.md) for the vocabulary and [`docs/`](docs) for the PRD and
ADRs.

## Why pysolated

There are alternatives that may be better in specific circumstances. To understand when pysolated is appropriate consider when not to use platform specific tools like bubblewrap and seatbelt or AI agent specific tools like Claude's /sandbox.

If you are using multiple different coding agents such as Claude and Codex and need to run on multiple different platforms such as Linux, MacOS or cloud hosted sandbox providers, and you want a consistent process in all cases, the pysolated will do the trick.

Why not use Sandcastle? If you are comfortable with Node or need to programatically instantiate agent loops from Typescript/Javascript code then Sancastle is the better choice. If you are more comfortable with Python or need to orchestrate agent loops from Python code, pysolate is functionally equivalent at the time of publication but built in Python

## Status

v1 iteration lifecycle: one agent provider (`claude_code`) running on the host
(`no_sandbox`), driven through `run()` and the `pysolated run` CLI. The run
loops up to `max_iterations`, stops early on a **completion signal**, enforces an
**idle timeout** and a post-signal **completion grace window**, reports the
**commits** the agent made, supports **abort** via an `asyncio.Event` (or
Ctrl-C from the CLI), and optionally extracts a schema-validated **structured
output** payload from the agent's prose. No isolation yet — the agent works
directly in your repo (real isolation is the next slice).

## Library

```python
import asyncio
from pysolated import run, claude_code, no_sandbox

async def main():
    result = await run(
        agent=claude_code("claude-opus-4-7"),
        sandbox=no_sandbox(),
        prompt="say hi",
    )
    print(result.stdout)
    print(result.branch, result.usage)

asyncio.run(main())
```

`run()` returns a frozen `RunResult` (`iterations`, `stdout`, `branch`, `usage`,
`completion_signal`, `commits`, `output`, `log_file_path`). The inline prompt
is sent to the agent verbatim — no substitution or expansion. Loop and timer
behavior is configurable:

```python
result = await run(
    agent=claude_code("claude-opus-4-7"),
    sandbox=no_sandbox(),
    prompt="Refactor X. Emit DONE when finished.",
    max_iterations=5,                  # default 1
    completion_signal="DONE",          # str or list[str]; default <promise>COMPLETE</promise>
    idle_timeout_seconds=600,          # fail if no output for this long
    completion_timeout_seconds=60,     # grace window after the signal is seen
)
```

### Prompt templates

A **prompt template** lives in a file and is resolved before the agent runs.
Pass it via `prompt_file=` (and `prompt_args=` for the values). `prompt` and
`prompt_file` are mutually exclusive; exactly one is required.

```python
# prompts/refactor.txt
#   Refactor the {{area}} module on branch {{branch}}.
#   Most recent commit: !`git log -1 --oneline`
#   Emit DONE when finished.

result = await run(
    agent=claude_code("claude-opus-4-7"),
    sandbox=no_sandbox(),
    prompt_file="prompts/refactor.txt",
    prompt_args={"area": "auth"},      # `branch` is built-in — don't pass it
    completion_signal="DONE",
)
```

Two stages run in order, both via the **sandbox seam** (so a Docker sandbox
later will resolve prompts inside the container too):

1. **Argument substitution** — every `{{KEY}}` is replaced. `prompt_args`
   overlay built-in arguments; for v1 the only built-in is `branch` (the
   current git branch, injected automatically). Validation is strict:
   - Passing a `prompt_args` key that collides with a built-in raises
     `PromptArgumentError` so you can't silently shadow framework values.
   - A `{{KEY}}` with no matching argument raises `PromptArgumentError` so
     a typo never reaches the agent as the literal `{{KEY}}` token.
2. **Prompt expansion** — every `` !`command` `` is replaced by the
   command's stdout, evaluated via the sandbox seam. A non-zero exit raises
   `PromptExpansionError` immediately, so a broken command never produces a
   silently-truncated prompt.

**Inline prompts skip both stages.** A literal `{{KEY}}` or `` !`cmd` `` in
an inline string is sent through verbatim — nothing is substituted or
executed. Passing `prompt_args` alongside an inline `prompt` raises
`PromptArgumentError` up front so you find out immediately that the args
would be ignored.

### File logging and run naming

By default `run()` narrates to the terminal via `TerminalDisplay`. For an
unattended run, redirect progress and agent output to a log file by passing
`log_file=` — `run()` constructs a `FileDisplay` that writes line-buffered, so
`tail -f` shows live progress while the run is in flight:

```python
result = await run(
    agent=claude_code("claude-opus-4-7"),
    sandbox=no_sandbox(),
    prompt="Refactor X. Emit DONE when finished.",
    name="nightly-refactor",            # appears in status lines + log header
    log_file="/tmp/pysolated-nightly.log",
)
print(result.log_file_path)             # /tmp/pysolated-nightly.log
```

- `log_file=` and `display=` are mutually exclusive — pass one or the other.
- `RunResult.log_file_path` is the resolved path of the log file when
  `log_file=` was used; `None` otherwise.
- `name=` is optional. When set it prefixes every status line in both
  `TerminalDisplay` and `FileDisplay` (`[nightly-refactor] Iteration 1/3`) and
  appears in the log file's first line so concurrent runs are distinguishable
  at a glance.
- Both displays satisfy the same `Display` protocol — the orchestrator is
  unchanged; it just receives a different impl.

### Abort / cancellation

A run can be cancelled mid-flight with an `asyncio.Event` passed as `signal=`.
Setting the event aborts the in-flight iteration: the `sandbox.exec` is
cancelled (on `no_sandbox` that kills the host subprocess) and `run()` raises
`asyncio.CancelledError` promptly instead of waiting for the agent to finish.

```python
import asyncio
from pysolated import run, claude_code, no_sandbox

async def main():
    abort = asyncio.Event()

    async def fire_abort_after(seconds: float) -> None:
        await asyncio.sleep(seconds)
        abort.set()

    asyncio.create_task(fire_abort_after(5.0))
    try:
        await run(
            agent=claude_code("claude-opus-4-7"),
            sandbox=no_sandbox(),
            prompt="long task",
            signal=abort,
        )
    except asyncio.CancelledError:
        print("aborted")

asyncio.run(main())
```

- Setting the event between iterations stops the outer loop before the next
  iteration starts.
- Pre-setting the event before `run()` is called aborts immediately, without
  invoking the agent.
- From the CLI, **Ctrl-C** maps onto the same abort signal: the SIGINT handler
  sets the event so the orchestrator cancels cleanly and the CLI exits with
  status `130` instead of tearing through asyncio with a `KeyboardInterrupt`.

### Agent failure surfacing

When the agent subprocess exits non-zero, `run()` raises `AgentExecutionError`
— a structured exception from the library's hierarchy, carrying the exit code
and the *tail* of stderr/stdout most likely to explain the crash:

```python
from pysolated import AgentExecutionError, run, claude_code, no_sandbox

try:
    await run(
        agent=claude_code("claude-opus-4-7"),
        sandbox=no_sandbox(),
        prompt="...",
    )
except AgentExecutionError as exc:
    print(exc.exit_code)     # e.g. 127
    print(exc.stderr_tail)   # last lines of stderr
    print(exc.stdout_tail)   # last lines of stream-json output
```

- `exit_code` is the subprocess exit status.
- `stderr_tail` / `stdout_tail` are truncated to the last ~50 lines so the
  exception stays readable even when the agent emitted megabytes of
  stream-json before crashing.
- `str(exc)` includes the exit code and the relevant tail — what the CLI
  echoes to the user (exit `1`).
- Distinct from `IdleTimeoutError` (a *stuck* agent) and
  `StructuredOutputError` (a malformed payload); each names its own failure
  mode so callers can branch on the cause.

### Structured output

A **structured output** is a schema-validated payload the agent emits inside
a named XML tag in its own prose. Ask for one via `output=` on `run()`:

```python
from pydantic import BaseModel
from pysolated import Output, run, claude_code, no_sandbox

class Answer(BaseModel):
    answer: int
    rationale: str

result = await run(
    agent=claude_code("claude-opus-4-7"),
    sandbox=no_sandbox(),
    prompt=(
        "Compute the answer. "
        "Reply with <result>{\"answer\": int, \"rationale\": str}</result>."
    ),
    output=Output.object("result", Answer),
)
print(result.output.answer)       # typed: int
print(result.output.rationale)    # typed: str
```

Two flavours, both consumed via `output=`:

- `Output.object(tag, model)` — JSON-parses the inner text and validates it
  against the Pydantic `model`. A `` ```json … ``` `` (or bare ` ``` … ``` `)
  fence inside the tag is unwrapped automatically.
- `Output.string(tag)` — returns the raw inner text, whitespace-trimmed; no
  JSON parsing, no schema.

When the agent emits the tag multiple times — common during self-correction —
the **last occurrence wins**.

Structured output is **orthogonal to the completion signal**; a run may use
either, both, or neither. Two up-front guards run before any agent work, so a
misconfigured call fails fast:

- `max_iterations != 1` is rejected (the payload must unambiguously belong to
  the iteration that produced it).
- The resolved prompt must contain the configured opening tag — a missing
  instruction is caught before paying for the run.

On a tag that does parse and validate, `RunResult.output` holds the model
instance (object mode) or the trimmed string (string mode). On failure (tag
missing, JSON parse error, schema mismatch), `run()` raises
`StructuredOutputError` carrying `tag`, `raw_matched` (the inner text when
any was found), and the underlying `cause` (the `JSONDecodeError` or
`pydantic.ValidationError`) so you can fix the prompt or schema without
re-running the agent.

## CLI

```bash
uv run pysolated run --prompt "say hi"
```

The CLI is a thin Typer layer over the same `run()` engine. Flags:

| Flag | Default | Description |
| --- | --- | --- |
| `--prompt` | _(see below)_ | Inline prompt, sent to the agent verbatim. Mutually exclusive with `--prompt-file`; one is required. |
| `--prompt-file` | _(see below)_ | Path to a prompt template. `{{KEY}}` placeholders are substituted from `--prompt-arg` overlaid on built-ins (`branch`); `` !`cmd` `` expressions are run through the sandbox and replaced by stdout. |
| `--prompt-arg` | _(none)_ | `KEY=VALUE` argument for `--prompt-file`. Repeatable. Rejected when combined with `--prompt`. |
| `--model` | `claude-opus-4-7` | Claude model to run. |
| `--cwd` | current dir | Repo directory to anchor the run to. |
| `--permission-mode` | _(none)_ | Claude `--permission-mode`; mutually exclusive with skip-permissions. |
| `--name` | _(none)_ | Optional name for the run. Appears as a `[name]` prefix on every status line and in the log file header. |
| `--log-file` | _(none)_ | Write progress and agent output to this path instead of the terminal. `tail -f` shows live progress; `RunResult.log_file_path` reports the path. |
| `--max-iterations` | `1` | Maximum agent invocations in the loop. |
| `--completion-signal` | `<promise>COMPLETE</promise>` | Substring in the agent's own output that ends the loop early (tool inputs/outputs it reads are never matched). Repeat the flag to match any of several. |
| `--idle-timeout` | `600` | Seconds without output before failing with an idle error. |
| `--completion-timeout` | `60` | Grace seconds after the completion signal before forcing success. |

Examples:

```bash
# multi-iteration run that stops as soon as the agent emits a custom signal
uv run pysolated run \
  --prompt "Refactor X. Emit DONE when finished." \
  --max-iterations 5 \
  --completion-signal DONE

# match either of two completion signals, against another repo
uv run pysolated run --prompt "..." --cwd /path/to/repo \
  --completion-signal DONE --completion-signal FINISHED

# shorten the idle timeout and the post-signal grace window
uv run pysolated run --prompt "..." --idle-timeout 120 --completion-timeout 30

# unattended run: write progress + agent output to a log file, name the run
uv run pysolated run --prompt "..." \
  --name nightly-refactor --log-file /tmp/pysolated-nightly.log

# resolve a prompt template, supplying one user argument; `branch` is built-in
uv run pysolated run \
  --prompt-file prompts/refactor.txt \
  --prompt-arg area=auth \
  --completion-signal DONE
```

On completion the CLI prints the iteration count, the matched completion signal,
the commits the agent made, and token usage.

## Architecture

Three injectable `Protocol` seams (per
[ADR 0002](docs/adr/0002-asyncio-not-effect.md)), built up front so Docker and
other agents are additive:

- **`AgentProvider`** — builds the agent command (an argv list + optional stdin,
  per [ADR 0001](docs/adr/0001-agent-providers-return-argv.md)) and parses its
  stream output.
- **`SandboxProvider`** — runs a command, streaming stdout line-by-line.
- **`Display`** — narrates the run (the orchestrator's test-substitution point).
  Two impls ship: `TerminalDisplay` (default) and `FileDisplay` (selected by
  `log_file=` / `--log-file`).

## Development

```bash
uv sync
uv run pytest
```
