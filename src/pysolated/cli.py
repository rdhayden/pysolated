"""Typer CLI — a thin layer over the `run()` engine.

`pysolated run` constructs the `claude_code` agent and `no_sandbox` sandbox and
calls the same `run()` the library exposes; it is never a parallel implementation.
"""

from __future__ import annotations

import asyncio
import signal as os_signal

import typer

from .agents import PermissionMode, claude_code
from .core import RunResult
from .errors import AgentExecutionError, IdleTimeoutError
from .orchestrator import (
    DEFAULT_COMPLETION_SIGNAL,
    DEFAULT_COMPLETION_TIMEOUT_SECONDS,
    DEFAULT_IDLE_TIMEOUT_SECONDS,
    run as run_engine,
)
from .prompts import PromptError
from .sandboxes import (
    _derive_default_image_name,
    build_image as build_image_helper,
    no_sandbox,
    remove_image as remove_image_helper,
)

app = typer.Typer(add_completion=False, help="Orchestrate Claude Code via run().")
podman_app = typer.Typer(
    add_completion=False,
    help="Podman image lifecycle helpers (build / remove the agent image).",
)
app.add_typer(podman_app, name="podman")

DEFAULT_MODEL = "claude-opus-4-7"


@app.callback()
def _root() -> None:
    """pysolated — orchestrate Claude Code via run().

    A no-op callback so Typer keeps `run` as an explicit subcommand
    (`pysolated run ...`) rather than collapsing it into the bare command.
    """


@app.command(name="run")
def run_command(
    prompt: str | None = typer.Option(
        None, "--prompt", help="Inline prompt, sent verbatim."
    ),
    prompt_file: str | None = typer.Option(
        None,
        "--prompt-file",
        help=(
            "Path to a prompt template; {{KEY}} placeholders are substituted "
            "from --prompt-arg and !`cmd` is expanded via the sandbox."
        ),
    ),
    prompt_arg: list[str] = typer.Option(
        [],
        "--prompt-arg",
        help=(
            "Argument as KEY=VALUE for a --prompt-file template. Repeatable. "
            "Rejected when used with --prompt."
        ),
    ),
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
    log_file: str | None = typer.Option(
        None,
        "--log-file",
        help=(
            "Write progress and agent output to this path instead of the terminal. "
            "tail -f shows live progress. RunResult.log_file_path reports the path."
        ),
    ),
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
    if prompt is None and prompt_file is None:
        typer.echo("error: pass either --prompt or --prompt-file.", err=True)
        raise typer.Exit(code=2)
    if prompt is not None and prompt_file is not None:
        typer.echo(
            "error: --prompt and --prompt-file are mutually exclusive.", err=True
        )
        raise typer.Exit(code=2)
    try:
        prompt_args = _parse_prompt_args(prompt_arg)
    except ValueError as exc:
        typer.echo(f"error: {exc}", err=True)
        raise typer.Exit(code=2)

    agent = claude_code(model, permission_mode=permission_mode)  # type: ignore[arg-type]
    sandbox = no_sandbox()

    signal_arg: str | list[str] = (
        completion_signal[0] if len(completion_signal) == 1 else completion_signal
    )

    async def _drive() -> RunResult:
        abort = asyncio.Event()
        loop = asyncio.get_running_loop()
        # Map SIGINT (Ctrl-C) onto the abort event so the orchestrator can
        # cancel the in-flight iteration and kill the agent subprocess
        # cleanly, instead of KeyboardInterrupt tearing through asyncio mid-await.
        try:
            loop.add_signal_handler(os_signal.SIGINT, abort.set)
            handler_installed = True
        except (NotImplementedError, RuntimeError):  # pragma: no cover - non-unix
            handler_installed = False
        try:
            return await run_engine(
                agent=agent,
                sandbox=sandbox,
                prompt=prompt,
                prompt_file=prompt_file,
                prompt_args=prompt_args or None,
                cwd=cwd,
                name=name,
                log_file=log_file,
                max_iterations=max_iterations,
                completion_signal=signal_arg,
                idle_timeout_seconds=idle_timeout,
                completion_timeout_seconds=completion_timeout,
                signal=abort,
            )
        finally:
            if handler_installed:
                try:
                    loop.remove_signal_handler(os_signal.SIGINT)
                except (NotImplementedError, RuntimeError):  # pragma: no cover
                    pass

    try:
        result = asyncio.run(_drive())
    except (asyncio.CancelledError, KeyboardInterrupt):
        typer.echo("Aborted.", err=True)
        raise typer.Exit(code=130)
    except PromptError as exc:
        typer.echo(str(exc), err=True)
        raise typer.Exit(code=2)
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


@podman_app.command("build-image")
def podman_build_image_command(
    file: str = typer.Option(
        "Containerfile",
        "--file",
        "-f",
        help="Containerfile path passed to `podman build -f`.",
    ),
    image: str | None = typer.Option(
        None,
        "--image",
        help="Image tag to build. Defaults to `pysolated:<sanitized-dirname>`.",
    ),
) -> None:
    """Build the Podman image used by `podman(...)`."""
    tag = image if image is not None else _derive_default_image_name()
    result = asyncio.run(build_image_helper(tag, containerfile=file))
    if result.exit_code != 0:
        typer.echo(
            result.stderr.strip() or f"podman build failed ({result.exit_code})",
            err=True,
        )
        raise typer.Exit(code=result.exit_code)
    typer.echo(f"Built {tag} from {file}")


@podman_app.command("remove-image")
def podman_remove_image_command(
    image: str | None = typer.Option(
        None,
        "--image",
        help="Image tag to remove. Defaults to `pysolated:<sanitized-dirname>`.",
    ),
) -> None:
    """Remove the Podman image (`podman rmi`)."""
    tag = image if image is not None else _derive_default_image_name()
    result = asyncio.run(remove_image_helper(tag))
    if result.exit_code != 0:
        typer.echo(
            result.stderr.strip() or f"podman rmi failed ({result.exit_code})", err=True
        )
        raise typer.Exit(code=result.exit_code)
    typer.echo(f"Removed {tag}")


def _parse_prompt_args(items: list[str]) -> dict[str, str]:
    """Parse `KEY=VALUE` strings into a dict, raising on malformed entries.

    Duplicate keys raise — repeating `--prompt-arg KEY=...` ambiguously is
    almost always a mistake (which value wins?), so surface it loudly.
    """
    result: dict[str, str] = {}
    for raw in items:
        if "=" not in raw:
            raise ValueError(f"--prompt-arg must be KEY=VALUE (got {raw!r})")
        key, _, value = raw.partition("=")
        key = key.strip()
        if not key:
            raise ValueError(f"--prompt-arg KEY may not be empty (got {raw!r})")
        if key in result:
            raise ValueError(f"--prompt-arg {key!r} was supplied more than once")
        result[key] = value
    return result


def main() -> None:  # pragma: no cover - console-script shim
    app()


__all__ = ["app", "main", "PermissionMode"]
