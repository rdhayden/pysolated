"""Typer CLI — a thin layer over the `run()` engine.

`pysolated run` constructs the `claude_code` agent and `no_sandbox` sandbox and
calls the same `run()` the library exposes; it is never a parallel implementation.
"""

from __future__ import annotations

import asyncio
import importlib
import signal as os_signal
from pathlib import Path

import typer

from .agents import PermissionMode
from .agents._registry import build_agent
from .core import RunResult
from .errors import (
    AgentExecutionError,
    BranchAlreadyCheckedOutError,
    IdleTimeoutError,
    MergeConflictError,
)
from .init import (
    ScaffoldExistsError,
    ScaffoldOptions,
    agent_names,
    default_model_for,
    sandbox_names,
    scaffold as scaffold_config_dir,
)
from .orchestrator import (
    DEFAULT_COMPLETION_SIGNAL,
    DEFAULT_COMPLETION_TIMEOUT_SECONDS,
    DEFAULT_IDLE_TIMEOUT_SECONDS,
    run as run_engine,
)
from .prompts import PromptError
from .sandboxes import (
    build_image as build_image_helper,
    no_sandbox,
    remove_image as remove_image_helper,
)
from .worktrees import (
    BranchStrategy,
    HeadStrategy,
    MergeToHeadStrategy,
    NamedBranchStrategy,
)
from .sandboxes._images import _derive_default_image_name
from .sandboxes.docker import (
    build_image as docker_build_image_helper,
    remove_image as docker_remove_image_helper,
)

# The `docker` submodule attribute is shadowed in the package namespace by the
# re-exported factory of the same name (see `sandboxes/__init__.py`); fetch the
# actual module via the import system so monkeypatch.setattr() against the
# module reaches the bindings the CLI's host-UID resolution uses.
_docker_module = importlib.import_module("pysolated.sandboxes.docker")

app = typer.Typer(add_completion=False, help="Orchestrate Claude Code via run().")
podman_app = typer.Typer(
    add_completion=False,
    help="Podman image lifecycle helpers (build / remove the agent image).",
)
app.add_typer(podman_app, name="podman")
docker_app = typer.Typer(
    add_completion=False,
    help="Docker image lifecycle helpers (build / remove the agent image).",
)
app.add_typer(docker_app, name="docker")

# Valid `--effort` values, in their canonical order. Validated up front in the
# CLI so an unknown value errors at the argument boundary (exit 2) instead of
# being forwarded to a provider that would have to re-validate it.
_VALID_EFFORTS = ("low", "medium", "high", "xhigh")

# Valid `--branch-strategy` values. The CLI is a thin layer over `run()`; the
# strategy name maps to the matching value-typed strategy below.
_VALID_BRANCH_STRATEGIES = ("head", "merge-to-head", "branch")


def _build_branch_strategy(name: str, *, branch: str | None) -> BranchStrategy:
    if name == "head":
        return HeadStrategy()
    if name == "merge-to-head":
        return MergeToHeadStrategy()
    if name == "branch":
        assert branch is not None  # validated upstream
        return NamedBranchStrategy(branch=branch)
    # Validated upstream — defensive default.
    raise ValueError(f"unknown branch strategy {name!r}")


@app.callback()
def _root() -> None:
    """pysolated — orchestrate Claude Code via run().

    A no-op callback so Typer keeps `run` as an explicit subcommand
    (`pysolated run ...`) rather than collapsing it into the bare command.
    """


@app.command(name="init")
def init_command(
    agent: str = typer.Option(
        "claude-code",
        "--agent",
        help=(
            "Agent to wire into the scaffolded driver "
            "(claude-code; codex lands in a follow-up slice)."
        ),
    ),
    sandbox: str = typer.Option(
        "podman",
        "--sandbox",
        help=(
            "Sandbox to wire into the scaffolded driver "
            "(podman; docker lands in a follow-up slice)."
        ),
    ),
    model: str | None = typer.Option(
        None,
        "--model",
        help=(
            "Model id baked into the scaffolded driver. "
            "Defaults to the chosen agent's default model."
        ),
    ),
    cwd: str | None = typer.Option(
        None,
        "--cwd",
        help="Repo directory the .pysolated/ config dir is created in.",
    ),
) -> None:
    """Scaffold a `.pysolated/` config directory into the current repo.

    Writes the **driver** (`main.py`), the skeleton **prompt template**
    (`prompt.md`), the `Containerfile`, `.gitignore`, and `.env.example` for
    the chosen agent × sandbox combo. Fails if `.pysolated/` already exists.
    """
    if agent not in agent_names():
        valid = ", ".join(agent_names())
        typer.echo(
            f"error: --agent {agent!r} is not registered. Valid agents: {valid}.",
            err=True,
        )
        raise typer.Exit(code=2)
    if sandbox not in sandbox_names():
        valid = ", ".join(sandbox_names())
        typer.echo(
            f"error: --sandbox {sandbox!r} is not registered. "
            f"Valid sandboxes: {valid}.",
            err=True,
        )
        raise typer.Exit(code=2)

    resolved_model = model if model is not None else default_model_for(agent)
    repo_dir = Path(cwd) if cwd is not None else Path.cwd()

    try:
        scaffold_config_dir(
            repo_dir,
            ScaffoldOptions(agent=agent, sandbox=sandbox, model=resolved_model),
        )
    except ScaffoldExistsError as exc:
        typer.echo(f"error: {exc}", err=True)
        raise typer.Exit(code=1)

    config_dir = repo_dir / ".pysolated"
    typer.echo(f"Scaffolded {config_dir}.")
    typer.echo("Next steps:")
    typer.echo(
        f"  1. cp {config_dir / '.env.example'} {config_dir / '.env'} "
        "and fill in your credentials"
    )
    typer.echo(f"  2. pysolated {sandbox} build-image")
    typer.echo(f"  3. python {config_dir / 'main.py'}")


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
    agent: str = typer.Option(
        "claude-code",
        "--agent",
        help=(
            "Which agent provider to drive. Defaults to claude-code; other "
            "registered agents resolve through the agent registry."
        ),
    ),
    model: str | None = typer.Option(
        None,
        "--model",
        help=(
            "Model id passed to the chosen agent. Defaults to claude-opus-4-7 "
            "for --agent claude-code; required for any other agent."
        ),
    ),
    effort: str | None = typer.Option(
        None,
        "--effort",
        help=(
            "Reasoning effort hint for agents that support it "
            f"({'|'.join(_VALID_EFFORTS)}). Rejected for --agent claude-code."
        ),
    ),
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
    branch_strategy: str = typer.Option(
        "head",
        "--branch-strategy",
        help=(
            "Where the agent's git work is placed "
            f"({'|'.join(_VALID_BRANCH_STRATEGIES)}). Defaults to head — commit "
            "directly on the current branch (today's behaviour)."
        ),
    ),
    branch: str | None = typer.Option(
        None,
        "--branch",
        help=(
            "Named branch for --branch-strategy branch (required for that "
            "strategy; rejected with head/merge-to-head)."
        ),
    ),
    copy_to_worktree: list[str] = typer.Option(
        [],
        "--copy-to-worktree",
        help=(
            "Host path (relative to --cwd) to reproduce inside the worktree "
            "before the agent starts. Repeatable. Rejected with "
            "--branch-strategy head (there is no worktree)."
        ),
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

    if effort is not None and effort not in _VALID_EFFORTS:
        typer.echo(
            f"error: --effort must be one of {'|'.join(_VALID_EFFORTS)} "
            f"(got {effort!r}).",
            err=True,
        )
        raise typer.Exit(code=2)

    if branch_strategy not in _VALID_BRANCH_STRATEGIES:
        typer.echo(
            f"error: --branch-strategy must be one of "
            f"{'|'.join(_VALID_BRANCH_STRATEGIES)} (got {branch_strategy!r}).",
            err=True,
        )
        raise typer.Exit(code=2)
    if branch_strategy == "branch" and branch is None:
        typer.echo(
            "error: --branch-strategy branch requires --branch <name>.", err=True
        )
        raise typer.Exit(code=2)
    if branch_strategy != "branch" and branch is not None:
        typer.echo(
            f"error: --branch is only valid with --branch-strategy branch "
            f"(got --branch-strategy {branch_strategy}).",
            err=True,
        )
        raise typer.Exit(code=2)
    # `--copy-to-worktree` only makes sense when a worktree is created
    # (mirrors the library-side rejection in run(); foreign-flag idiom).
    if copy_to_worktree and branch_strategy == "head":
        typer.echo(
            "error: --copy-to-worktree requires --branch-strategy "
            "merge-to-head or branch (got head).",
            err=True,
        )
        raise typer.Exit(code=2)
    strategy = _build_branch_strategy(branch_strategy, branch=branch)

    try:
        agent_provider = build_agent(
            agent,
            model=model,
            effort=effort,
            permission_mode=permission_mode,  # type: ignore[arg-type]
        )
    except ValueError as exc:
        typer.echo(f"error: {exc}", err=True)
        raise typer.Exit(code=2)
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
                agent=agent_provider,
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
                branch_strategy=strategy,
                copy_to_worktree=copy_to_worktree or None,
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
    except ValueError as exc:
        # Up-front configuration/validation errors raised by run() before the
        # agent starts — e.g. a bad `--copy-to-worktree` path (missing,
        # absolute, or `..`-escaping) or an incompatible strategy/sandbox
        # combination. These are usage errors, so they exit 2 with a clean
        # message rather than leaking a traceback, mirroring PromptError and
        # the other arg-validation rejections above.
        typer.echo(f"error: {exc}", err=True)
        raise typer.Exit(code=2)
    except (
        AgentExecutionError,
        BranchAlreadyCheckedOutError,
        IdleTimeoutError,
        MergeConflictError,
    ) as exc:
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


@docker_app.command("build-image")
def docker_build_image_command(
    file: str = typer.Option(
        "Dockerfile",
        "--file",
        "-f",
        help="Dockerfile path passed to `docker build -f`.",
    ),
    image: str | None = typer.Option(
        None,
        "--image",
        help="Image tag to build. Defaults to `pysolated:<sanitized-dirname>`.",
    ),
    build_arg: list[str] = typer.Option(
        [],
        "--build-arg",
        help=(
            "Extra `docker build --build-arg KEY=VALUE`. Repeatable. "
            "Overrides the auto-injected AGENT_UID/AGENT_GID."
        ),
    ),
) -> None:
    """Build the Docker image used by `docker(...)`.

    `AGENT_UID`/`AGENT_GID` are auto-injected from the host UID/GID so a
    no-argument build produces a correctly-aligned image (ADR 0005). An
    explicit `--build-arg AGENT_UID=…` overrides the auto-injected default.
    """
    try:
        build_args = _resolve_docker_build_args(build_arg)
    except ValueError as exc:
        typer.echo(f"error: {exc}", err=True)
        raise typer.Exit(code=2)

    tag = image if image is not None else _derive_default_image_name()
    result = asyncio.run(
        docker_build_image_helper(tag, containerfile=file, build_args=build_args)
    )
    if result.exit_code != 0:
        typer.echo(
            result.stderr.strip() or f"docker build failed ({result.exit_code})",
            err=True,
        )
        raise typer.Exit(code=result.exit_code)
    typer.echo(f"Built {tag} from {file}")


@docker_app.command("remove-image")
def docker_remove_image_command(
    image: str | None = typer.Option(
        None,
        "--image",
        help="Image tag to remove. Defaults to `pysolated:<sanitized-dirname>`.",
    ),
) -> None:
    """Remove the Docker image (`docker rmi`)."""
    tag = image if image is not None else _derive_default_image_name()
    result = asyncio.run(docker_remove_image_helper(tag))
    if result.exit_code != 0:
        typer.echo(
            result.stderr.strip() or f"docker rmi failed ({result.exit_code})", err=True
        )
        raise typer.Exit(code=result.exit_code)
    typer.echo(f"Removed {tag}")


def _resolve_docker_build_args(items: list[str]) -> dict[str, str]:
    """Auto-inject host `AGENT_UID`/`AGENT_GID`, then layer `--build-arg` overrides on top.

    Explicit `--build-arg AGENT_UID=…` wins because user entries are merged
    last; arbitrary extra entries pass straight through to `docker build`.
    Parsing mirrors `_parse_prompt_args` but accepts duplicates (last wins) —
    `docker build` itself accepts the same `--build-arg KEY=…` repeated and
    keeps the last value, so doing otherwise here would be a footgun.
    """
    resolved: dict[str, str] = {
        "AGENT_UID": str(_docker_module._resolve_host_uid()),
        "AGENT_GID": str(_docker_module._resolve_host_gid()),
    }
    for raw in items:
        if "=" not in raw:
            raise ValueError(f"--build-arg must be KEY=VALUE (got {raw!r})")
        key, _, value = raw.partition("=")
        key = key.strip()
        if not key:
            raise ValueError(f"--build-arg KEY may not be empty (got {raw!r})")
        resolved[key] = value
    return resolved


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
