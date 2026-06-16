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

## CLI

```bash
uv run pysolated run --prompt "say hi"
```

The CLI is a thin Typer layer over the same `run()` engine. Flags:

| Flag | Default | Description |
| --- | --- | --- |
| `--prompt` | _(required)_ | Inline prompt, sent to the agent verbatim. |
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
