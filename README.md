# pysolated

A Python toolkit that orchestrates AI coding agents inside sandboxes. A Python
reimagining of [Sandcastle](https://github.com/ai-hero-dev/sandcastle); see
[`CONTEXT.md`](CONTEXT.md) for the vocabulary and [`docs/`](docs) for the PRD and
ADRs.

## Status

v1 iteration lifecycle: one agent provider (`claude_code`) running on the host
(`no_sandbox`), driven through `run()` and the `pysolated run` CLI. The run
loops up to `max_iterations`, stops early on a **completion signal**, enforces an
**idle timeout** and a post-signal **completion grace window**, and reports the
**commits** the agent made. No isolation yet — the agent works directly in your
repo (real isolation is the next slice).

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
`completion_signal`, `commits`). The inline prompt is sent to the agent verbatim
— no substitution or expansion. Loop and timer behavior is configurable:

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
| `--name` | _(none)_ | Optional name for the run, shown in the display. |
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

## Development

```bash
uv sync
uv run pytest
```
