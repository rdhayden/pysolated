"""Typer CLI — a thin layer over the `run()` engine.

`pysolated run` constructs the `claude_code` agent and `no_sandbox` sandbox and
calls the same `run()` the library exposes; it is never a parallel implementation.
"""

from __future__ import annotations

import asyncio

import typer

from .agents import PermissionMode, claude_code
from .errors import AgentExecutionError, IdleTimeoutError
from .orchestrator import (
    DEFAULT_COMPLETION_SIGNAL,
    DEFAULT_COMPLETION_TIMEOUT_SECONDS,
    DEFAULT_IDLE_TIMEOUT_SECONDS,
    run as run_engine,
)
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
    max_iterations: int = typer.Option(
        1, "--max-iterations", min=1, help="Max agent invocations in the loop."
    ),
    completion_signal: list[str] = typer.Option(
        [DEFAULT_COMPLETION_SIGNAL],
        "--completion-signal",
        help="Substring(s) that end the loop early. Repeat for multiple candidates.",
    ),
    idle_timeout: float = typer.Option(
        DEFAULT_IDLE_TIMEOUT_SECONDS,
        "--idle-timeout",
        help="Seconds without output before failing with an idle error.",
    ),
    completion_timeout: float = typer.Option(
        DEFAULT_COMPLETION_TIMEOUT_SECONDS,
        "--completion-timeout",
        help="Grace seconds after the completion signal before forcing success.",
    ),
) -> None:
    """Drive Claude Code on the host and print the result."""
    agent = claude_code(model, permission_mode=permission_mode)  # type: ignore[arg-type]
    sandbox = no_sandbox()

    signal_arg: str | list[str] = (
        completion_signal[0] if len(completion_signal) == 1 else completion_signal
    )

    try:
        result = asyncio.run(
            run_engine(
                agent=agent,
                sandbox=sandbox,
                prompt=prompt,
                cwd=cwd,
                name=name,
                max_iterations=max_iterations,
                completion_signal=signal_arg,
                idle_timeout_seconds=idle_timeout,
                completion_timeout_seconds=completion_timeout,
            )
        )
    except KeyboardInterrupt:  # pragma: no cover - interactive abort
        typer.echo("Aborted.", err=True)
        raise typer.Exit(code=130)
    except (AgentExecutionError, IdleTimeoutError) as exc:
        typer.echo(str(exc), err=True)
        raise typer.Exit(code=1)

    typer.echo(
        f"Iterations: {result.iterations} / {max_iterations}; "
        f"completion signal: {result.completion_signal or '(none)'}"
    )
    if result.commits:
        typer.echo("Commits: " + ", ".join(result.commits))
    else:
        typer.echo("Commits: (none)")
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
