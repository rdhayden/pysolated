"""Typer CLI — a thin layer over the `run()` engine.

`pysolated run` constructs the `claude_code` agent and `no_sandbox` sandbox and
calls the same `run()` the library exposes; it is never a parallel implementation.
"""

from __future__ import annotations

import asyncio

import typer

from .agents import PermissionMode, claude_code
from .errors import AgentExecutionError
from .orchestrator import run as run_engine
from .sandboxes import no_sandbox

app = typer.Typer(add_completion=False, help="Orchestrate Claude Code via run().")

DEFAULT_MODEL = "claude-opus-4-7"


@app.callback()
def _root() -> None:
    """pysolated — orchestrate Claude Code via run().

    A no-op callback so Typer keeps `run` as an explicit subcommand
    (`pysolated run ...`) rather than collapsing it into the bare command.
    """


@app.command(name="run")
def run_command(
    prompt: str = typer.Option(..., "--prompt", help="Inline prompt, sent verbatim."),
    model: str = typer.Option(DEFAULT_MODEL, "--model", help="Claude model to run."),
    cwd: str | None = typer.Option(
        None, "--cwd", help="Repo directory to anchor the run to."
    ),
    permission_mode: str | None = typer.Option(
        None,
        "--permission-mode",
        help="Claude --permission-mode (mutually exclusive with skip-permissions).",
    ),
    name: str | None = typer.Option(None, "--name", help="Optional name for the run."),
) -> None:
    """Drive Claude Code once on the host and print the result."""
    agent = claude_code(model, permission_mode=permission_mode)  # type: ignore[arg-type]
    sandbox = no_sandbox()

    try:
        result = asyncio.run(
            run_engine(
                agent=agent,
                sandbox=sandbox,
                prompt=prompt,
                cwd=cwd,
                name=name,
            )
        )
    except KeyboardInterrupt:  # pragma: no cover - interactive abort
        typer.echo("Aborted.", err=True)
        raise typer.Exit(code=130)
    except AgentExecutionError as exc:
        typer.echo(str(exc), err=True)
        raise typer.Exit(code=1)

    if result.usage is not None:
        typer.echo(
            f"Tokens — in: {result.usage.input_tokens}, "
            f"out: {result.usage.output_tokens}, "
            f"cache read: {result.usage.cache_read_input_tokens}, "
            f"cache creation: {result.usage.cache_creation_input_tokens}"
        )
    else:
        typer.echo("Token usage: unavailable")


def main() -> None:  # pragma: no cover - console-script shim
    app()


__all__ = ["app", "main", "PermissionMode"]
