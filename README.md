# pysolated

A Python toolkit that orchestrates AI coding agents inside sandboxes. A Python
reimagining of [Sandcastle](https://github.com/ai-hero-dev/sandcastle); see
[`CONTEXT.md`](CONTEXT.md) for the vocabulary and [`docs/`](docs) for the PRD and
ADRs.

## Status

Walking skeleton (v1, single iteration): one agent provider (`claude_code`)
running on the host (`no_sandbox`), driven once through `run()` and the
`pysolated run` CLI. No isolation yet — the agent works directly in your
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

`run()` returns a frozen `RunResult` (`iterations`, `stdout`, `branch`,
`usage`). The inline prompt is sent to the agent verbatim — no substitution or
expansion.

## CLI

```bash
pysolated run --prompt "say hi"
pysolated run --prompt "..." --model claude-opus-4-7 --cwd /path/to/repo
```

The CLI is a thin Typer layer over the same `run()` engine.

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
