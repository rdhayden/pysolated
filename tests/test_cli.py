"""CLI flag tests — verify --log-file and --name flow into run_engine().

We patch the engine import inside `pysolated.cli` to capture the kwargs the
CLI assembled. That keeps the CLI surface (Typer wiring + flag parsing) tested
without running a real agent.
"""

from __future__ import annotations

import asyncio
import os
import signal as os_signal
import sys
from pathlib import Path
from typing import Any

import pysolated.sandboxes.docker  # noqa: F401  (ensure submodule in sys.modules)
import pysolated.sandboxes.podman  # noqa: F401  (ensure submodule in sys.modules)
from typer.testing import CliRunner

from pysolated import RunResult
from pysolated import cli as cli_module

# `pysolated.sandboxes.podman` (attribute) resolves to the re-exported factory
# function; the submodule of the same name is shadowed in the package
# namespace. Reach the submodule via `sys.modules` so monkey-patching hits
# the binding the CLI's `build_image`/`remove_image` helpers actually use.
_podman_module = sys.modules["pysolated.sandboxes.podman"]
_docker_module = sys.modules["pysolated.sandboxes.docker"]


def _fake_engine_capturing_kwargs(captured: dict) -> Any:
    async def fake_run(**kwargs: Any) -> RunResult:
        captured.update(kwargs)
        return RunResult(
            iterations=1,
            stdout="",
            branch="main",
            log_file_path=str(kwargs.get("log_file") or "") or None,
        )

    return fake_run


def test_cli_log_file_flag_passes_path_to_engine(
    tmp_path: Path, monkeypatch: Any
) -> None:
    captured: dict = {}
    monkeypatch.setattr(
        cli_module, "run_engine", _fake_engine_capturing_kwargs(captured)
    )
    log_path = tmp_path / "run.log"

    runner = CliRunner()
    result = runner.invoke(
        cli_module.app,
        ["run", "--prompt", "say hi", "--log-file", str(log_path)],
    )
    assert result.exit_code == 0, result.output
    assert captured["log_file"] == str(log_path)


def test_cli_name_flag_passes_through_to_engine(monkeypatch: Any) -> None:
    captured: dict = {}
    monkeypatch.setattr(
        cli_module, "run_engine", _fake_engine_capturing_kwargs(captured)
    )

    runner = CliRunner()
    result = runner.invoke(
        cli_module.app,
        ["run", "--prompt", "say hi", "--name", "alpha"],
    )
    assert result.exit_code == 0, result.output
    assert captured["name"] == "alpha"


def test_cli_passes_abort_signal_event_to_engine(monkeypatch: Any) -> None:
    """The CLI must hand `signal=asyncio.Event` to `run_engine` for SIGINT wiring."""
    captured: dict = {}
    monkeypatch.setattr(
        cli_module, "run_engine", _fake_engine_capturing_kwargs(captured)
    )

    runner = CliRunner()
    result = runner.invoke(cli_module.app, ["run", "--prompt", "go"])
    assert result.exit_code == 0, result.output
    assert isinstance(captured["signal"], asyncio.Event)


def test_cli_sigint_sets_abort_signal_and_exits_130(monkeypatch: Any) -> None:
    """SIGINT during a run must set the abort event and surface exit 130.

    The fake engine schedules a SIGINT to the current process while it is
    awaiting, then verifies the CLI's installed handler set the event before
    raising `CancelledError` (mimicking the orchestrator's abort path).
    """
    captured: dict = {}

    async def fake_engine(**kwargs: Any) -> RunResult:
        captured.update(kwargs)
        abort: asyncio.Event = kwargs["signal"]
        loop = asyncio.get_running_loop()
        loop.call_soon(lambda: os.kill(os.getpid(), os_signal.SIGINT))
        try:
            await asyncio.wait_for(abort.wait(), timeout=2.0)
        except asyncio.TimeoutError:  # pragma: no cover - assertion path
            raise AssertionError("CLI did not set the abort event on SIGINT")
        raise asyncio.CancelledError("aborted by signal")

    monkeypatch.setattr(cli_module, "run_engine", fake_engine)

    runner = CliRunner()
    result = runner.invoke(cli_module.app, ["run", "--prompt", "go"])
    assert result.exit_code == 130, result.output
    assert isinstance(captured["signal"], asyncio.Event)


def test_cli_agent_execution_error_exits_1_with_diagnostic_on_stderr(
    monkeypatch: Any,
) -> None:
    """A crashed agent yields exit 1 and prints the stderr/output tail.

    Without the tail on stderr the user just sees "Iterations: 1 / 1" or a
    bare traceback — neither tells them why the agent died.
    """
    from pysolated.errors import AgentExecutionError

    async def crashing_engine(**kwargs: Any) -> RunResult:
        raise AgentExecutionError(
            exit_code=42,
            stderr="ENOENT: cannot find 'claude' binary",
            stdout_tail="",
        )

    monkeypatch.setattr(cli_module, "run_engine", crashing_engine)

    runner = CliRunner()
    result = runner.invoke(cli_module.app, ["run", "--prompt", "go"])
    assert result.exit_code == 1, result.output
    # The tail must reach the user (typer.echo with err=True; CliRunner mixes
    # stdout+stderr into `output` by default).
    assert "ENOENT" in result.output
    assert "claude" in result.output
    assert "42" in result.output


def test_cli_branch_already_checked_out_error_exits_1_without_traceback(
    monkeypatch: Any,
) -> None:
    """A `branch` run naming an already-checked-out branch exits 1, no traceback.

    The engine raises `BranchAlreadyCheckedOutError` from `prepare`; the CLI
    must surface its clear message on stderr and exit 1 — not let it propagate
    as an uncaught exception dumping a Rich traceback over the message.
    """
    from pysolated.errors import BranchAlreadyCheckedOutError

    async def checked_out_engine(**kwargs: Any) -> RunResult:
        raise BranchAlreadyCheckedOutError(branch="main")

    monkeypatch.setattr(cli_module, "run_engine", checked_out_engine)

    runner = CliRunner()
    result = runner.invoke(
        cli_module.app,
        ["run", "--prompt", "go", "--branch-strategy", "branch", "--branch", "main"],
    )
    assert result.exit_code == 1, result.output
    assert "already checked out" in result.output
    # The clean message reaches the user without a propagated traceback (the
    # only exception is typer's own SystemExit from the clean exit-1 path).
    assert "Traceback" not in result.output
    assert isinstance(result.exception, SystemExit)


def test_cli_copy_to_worktree_validation_error_exits_2_without_traceback(
    monkeypatch: Any,
) -> None:
    """A bad `--copy-to-worktree` path exits 2 with a clean message, no traceback.

    `run()` validates copy paths up front and raises a plain `ValueError`
    (missing / absolute / `..`-escaping source). The CLI must surface that as
    an `error: ...` usage error on stderr and exit 2 — not let it propagate as
    an uncaught Rich traceback.
    """

    async def validation_failing_engine(**kwargs: Any) -> RunResult:
        raise ValueError("copy_to_worktree path does not exist in cwd: 'nope'")

    monkeypatch.setattr(cli_module, "run_engine", validation_failing_engine)

    runner = CliRunner()
    result = runner.invoke(
        cli_module.app,
        [
            "run",
            "--prompt",
            "go",
            "--branch-strategy",
            "merge-to-head",
            "--copy-to-worktree",
            "nope",
        ],
    )
    assert result.exit_code == 2, result.output
    assert "error:" in result.output
    assert "does not exist" in result.output
    # Clean exit — the message reaches the user without a propagated traceback.
    assert "Traceback" not in result.output
    assert isinstance(result.exception, SystemExit)


def test_cli_branch_strategy_branch_requires_branch_flag(monkeypatch: Any) -> None:
    """`--branch-strategy branch` without `--branch <name>` exits 2."""
    captured: dict = {}
    monkeypatch.setattr(
        cli_module, "run_engine", _fake_engine_capturing_kwargs(captured)
    )

    runner = CliRunner()
    result = runner.invoke(
        cli_module.app,
        ["run", "--prompt", "go", "--branch-strategy", "branch"],
    )
    assert result.exit_code == 2, result.output
    assert "--branch" in result.output
    # The engine must NOT have been called when arg parsing rejected it.
    assert captured == {}


def test_cli_branch_strategy_branch_with_branch_flag_passes_named_strategy(
    monkeypatch: Any,
) -> None:
    """`--branch-strategy branch --branch X` builds NamedBranchStrategy(branch=X)."""
    from pysolated import NamedBranchStrategy

    captured: dict = {}
    monkeypatch.setattr(
        cli_module, "run_engine", _fake_engine_capturing_kwargs(captured)
    )

    runner = CliRunner()
    result = runner.invoke(
        cli_module.app,
        [
            "run",
            "--prompt",
            "go",
            "--branch-strategy",
            "branch",
            "--branch",
            "feature/x",
        ],
    )
    assert result.exit_code == 0, result.output
    strategy = captured["branch_strategy"]
    assert isinstance(strategy, NamedBranchStrategy)
    assert strategy.branch == "feature/x"


def test_cli_branch_flag_rejected_with_head_strategy(monkeypatch: Any) -> None:
    """`--branch` with `--branch-strategy head` exits 2 (foreign-flag rejection)."""
    captured: dict = {}
    monkeypatch.setattr(
        cli_module, "run_engine", _fake_engine_capturing_kwargs(captured)
    )

    runner = CliRunner()
    result = runner.invoke(
        cli_module.app,
        [
            "run",
            "--prompt",
            "go",
            "--branch-strategy",
            "head",
            "--branch",
            "feature/x",
        ],
    )
    assert result.exit_code == 2, result.output
    assert "--branch" in result.output
    assert captured == {}


def test_cli_branch_flag_rejected_with_merge_to_head_strategy(monkeypatch: Any) -> None:
    """`--branch` with `--branch-strategy merge-to-head` exits 2."""
    captured: dict = {}
    monkeypatch.setattr(
        cli_module, "run_engine", _fake_engine_capturing_kwargs(captured)
    )

    runner = CliRunner()
    result = runner.invoke(
        cli_module.app,
        [
            "run",
            "--prompt",
            "go",
            "--branch-strategy",
            "merge-to-head",
            "--branch",
            "feature/x",
        ],
    )
    assert result.exit_code == 2, result.output
    assert "--branch" in result.output
    assert captured == {}


def test_cli_copy_to_worktree_repeatable_maps_to_list(monkeypatch: Any) -> None:
    """`--copy-to-worktree A --copy-to-worktree B` becomes `copy_to_worktree=[A, B]`."""
    captured: dict = {}
    monkeypatch.setattr(
        cli_module, "run_engine", _fake_engine_capturing_kwargs(captured)
    )

    runner = CliRunner()
    result = runner.invoke(
        cli_module.app,
        [
            "run",
            "--prompt",
            "go",
            "--branch-strategy",
            "merge-to-head",
            "--copy-to-worktree",
            ".env",
            "--copy-to-worktree",
            "node_modules",
        ],
    )
    assert result.exit_code == 0, result.output
    assert captured["copy_to_worktree"] == [".env", "node_modules"]


def test_cli_copy_to_worktree_rejected_with_head_strategy(monkeypatch: Any) -> None:
    """`--copy-to-worktree` with `--branch-strategy head` exits 2 (foreign-flag idiom)."""
    captured: dict = {}
    monkeypatch.setattr(
        cli_module, "run_engine", _fake_engine_capturing_kwargs(captured)
    )

    runner = CliRunner()
    result = runner.invoke(
        cli_module.app,
        [
            "run",
            "--prompt",
            "go",
            "--branch-strategy",
            "head",
            "--copy-to-worktree",
            ".env",
        ],
    )
    assert result.exit_code == 2, result.output
    assert "--copy-to-worktree" in result.output
    # The engine must NOT have been called when arg parsing rejected the combo.
    assert captured == {}


def test_cli_copy_to_worktree_accepted_with_branch_strategy(monkeypatch: Any) -> None:
    """`--copy-to-worktree` is accepted with `--branch-strategy branch`."""
    captured: dict = {}
    monkeypatch.setattr(
        cli_module, "run_engine", _fake_engine_capturing_kwargs(captured)
    )

    runner = CliRunner()
    result = runner.invoke(
        cli_module.app,
        [
            "run",
            "--prompt",
            "go",
            "--branch-strategy",
            "branch",
            "--branch",
            "feature/x",
            "--copy-to-worktree",
            ".env",
        ],
    )
    assert result.exit_code == 0, result.output
    assert captured["copy_to_worktree"] == [".env"]


def test_cli_omitting_copy_to_worktree_passes_none(monkeypatch: Any) -> None:
    """Default behaviour unchanged: no `--copy-to-worktree` → kwarg is None."""
    captured: dict = {}
    monkeypatch.setattr(
        cli_module, "run_engine", _fake_engine_capturing_kwargs(captured)
    )

    runner = CliRunner()
    result = runner.invoke(cli_module.app, ["run", "--prompt", "go"])
    assert result.exit_code == 0, result.output
    assert captured.get("copy_to_worktree") is None


def test_cli_default_branch_strategy_unchanged_head(monkeypatch: Any) -> None:
    """Default branch strategy with no flags is still `head` (regression)."""
    from pysolated import HeadStrategy

    captured: dict = {}
    monkeypatch.setattr(
        cli_module, "run_engine", _fake_engine_capturing_kwargs(captured)
    )

    runner = CliRunner()
    result = runner.invoke(cli_module.app, ["run", "--prompt", "go"])
    assert result.exit_code == 0, result.output
    assert isinstance(captured["branch_strategy"], HeadStrategy)


def test_cli_without_log_file_omits_flag(monkeypatch: Any) -> None:
    captured: dict = {}
    monkeypatch.setattr(
        cli_module, "run_engine", _fake_engine_capturing_kwargs(captured)
    )

    runner = CliRunner()
    result = runner.invoke(cli_module.app, ["run", "--prompt", "say hi"])
    assert result.exit_code == 0, result.output
    assert captured.get("log_file") is None


# ---------------------------------------------------------------------------
# `pysolated podman build-image` / `remove-image` (issue #22).
# ---------------------------------------------------------------------------


def _fake_subprocess_recorder() -> tuple[list[list[str]], Any]:
    """Record argv from `_stream_subprocess` for podman-CLI shape assertions."""
    from pysolated import ExecResult

    calls: list[list[str]] = []

    async def fake(argv: list[str], **kwargs: Any) -> ExecResult:
        calls.append(list(argv))
        return ExecResult(exit_code=0, stdout="", stderr="")

    return calls, fake


def test_cli_podman_build_image_default_containerfile(
    tmp_path: Path, monkeypatch: Any
) -> None:
    """`pysolated podman build-image` runs `podman build -f Containerfile -t <derived>`."""
    project = tmp_path / "demo"
    project.mkdir()
    monkeypatch.chdir(project)
    calls, fake = _fake_subprocess_recorder()
    monkeypatch.setattr(_podman_module, "_stream_subprocess", fake)

    runner = CliRunner()
    result = runner.invoke(cli_module.app, ["podman", "build-image"])
    assert result.exit_code == 0, result.output
    assert calls == [
        [
            "podman",
            "build",
            "-f",
            "Containerfile",
            "-t",
            "pysolated:demo",
            str(project),
        ]
    ]


def test_cli_podman_build_image_uses_scaffolded_containerfile_by_default(
    tmp_path: Path, monkeypatch: Any
) -> None:
    """After `pysolated init`, the no-flag build command uses `.pysolated/Containerfile`."""
    project = tmp_path / "demo"
    config = project / ".pysolated"
    config.mkdir(parents=True)
    (config / "Containerfile").write_text("FROM scratch\n", encoding="utf-8")
    monkeypatch.chdir(project)
    calls, fake = _fake_subprocess_recorder()
    monkeypatch.setattr(_podman_module, "_stream_subprocess", fake)

    runner = CliRunner()
    result = runner.invoke(cli_module.app, ["podman", "build-image"])
    assert result.exit_code == 0, result.output
    argv = calls[0]
    i = argv.index("-f")
    assert argv[i + 1] == ".pysolated/Containerfile"


def test_cli_podman_build_image_custom_file_flag(
    tmp_path: Path, monkeypatch: Any
) -> None:
    """`--file` overrides the Containerfile path."""
    project = tmp_path / "demo"
    project.mkdir()
    monkeypatch.chdir(project)
    calls, fake = _fake_subprocess_recorder()
    monkeypatch.setattr(_podman_module, "_stream_subprocess", fake)

    runner = CliRunner()
    result = runner.invoke(
        cli_module.app,
        ["podman", "build-image", "--file", "docker/Dev.Containerfile"],
    )
    assert result.exit_code == 0, result.output
    argv = calls[0]
    i = argv.index("-f")
    assert argv[i + 1] == "docker/Dev.Containerfile"


def test_cli_podman_build_image_explicit_tag(tmp_path: Path, monkeypatch: Any) -> None:
    """An explicit `--image` skips derivation."""
    monkeypatch.chdir(tmp_path)
    calls, fake = _fake_subprocess_recorder()
    monkeypatch.setattr(_podman_module, "_stream_subprocess", fake)

    runner = CliRunner()
    result = runner.invoke(
        cli_module.app,
        ["podman", "build-image", "--image", "custom:tag"],
    )
    assert result.exit_code == 0, result.output
    argv = calls[0]
    i = argv.index("-t")
    assert argv[i + 1] == "custom:tag"


def test_cli_podman_remove_image_default_tag(tmp_path: Path, monkeypatch: Any) -> None:
    """`pysolated podman remove-image` runs `podman rmi <derived>` by default."""
    project = tmp_path / "demo"
    project.mkdir()
    monkeypatch.chdir(project)
    calls, fake = _fake_subprocess_recorder()
    monkeypatch.setattr(_podman_module, "_stream_subprocess", fake)

    runner = CliRunner()
    result = runner.invoke(cli_module.app, ["podman", "remove-image"])
    assert result.exit_code == 0, result.output
    assert calls == [["podman", "rmi", "pysolated:demo"]]


def test_cli_podman_remove_image_explicit_tag(tmp_path: Path, monkeypatch: Any) -> None:
    monkeypatch.chdir(tmp_path)
    calls, fake = _fake_subprocess_recorder()
    monkeypatch.setattr(_podman_module, "_stream_subprocess", fake)

    runner = CliRunner()
    result = runner.invoke(
        cli_module.app,
        ["podman", "remove-image", "--image", "custom:tag"],
    )
    assert result.exit_code == 0, result.output
    assert calls == [["podman", "rmi", "custom:tag"]]


def test_cli_podman_build_image_nonzero_exit_propagates(
    tmp_path: Path, monkeypatch: Any
) -> None:
    """A failing `podman build` surfaces a non-zero exit and the stderr."""
    from pysolated import ExecResult

    monkeypatch.chdir(tmp_path)

    async def failing(argv: list[str], **kwargs: Any) -> ExecResult:
        return ExecResult(exit_code=2, stdout="", stderr="syntax error")

    monkeypatch.setattr(_podman_module, "_stream_subprocess", failing)
    runner = CliRunner()
    result = runner.invoke(cli_module.app, ["podman", "build-image"])
    assert result.exit_code == 2, result.output
    assert "syntax error" in result.output


# ---------------------------------------------------------------------------
# `pysolated docker build-image` / `remove-image` (issue #27).
# ---------------------------------------------------------------------------


def _fake_docker_subprocess_recorder() -> tuple[list[list[str]], Any]:
    """Record argv from `_stream_subprocess` on the docker submodule."""
    from pysolated import ExecResult

    calls: list[list[str]] = []

    async def fake(argv: list[str], **kwargs: Any) -> ExecResult:
        calls.append(list(argv))
        return ExecResult(exit_code=0, stdout="", stderr="")

    return calls, fake


def test_cli_docker_build_image_auto_injects_host_uid_gid(
    tmp_path: Path, monkeypatch: Any
) -> None:
    """`pysolated docker build-image` auto-injects `AGENT_UID=<host>`/`AGENT_GID=<host>`."""
    project = tmp_path / "demo"
    project.mkdir()
    monkeypatch.chdir(project)
    monkeypatch.setattr(_docker_module, "_resolve_host_uid", lambda: 1500)
    monkeypatch.setattr(_docker_module, "_resolve_host_gid", lambda: 1501)
    calls, fake = _fake_docker_subprocess_recorder()
    monkeypatch.setattr(_docker_module, "_stream_subprocess", fake)

    runner = CliRunner()
    result = runner.invoke(cli_module.app, ["docker", "build-image"])
    assert result.exit_code == 0, result.output

    argv = calls[0]
    assert argv[0:2] == ["docker", "build"]
    pairs = [argv[i + 1] for i, a in enumerate(argv) if a == "--build-arg"]
    assert "AGENT_UID=1500" in pairs
    assert "AGENT_GID=1501" in pairs
    # The derived tag and the cwd context still come through.
    assert argv[-3:] == ["-t", "pysolated:demo", str(project)]


def test_cli_docker_build_image_uses_scaffolded_dockerfile_by_default(
    tmp_path: Path, monkeypatch: Any
) -> None:
    """After `pysolated init`, the no-flag build command uses `.pysolated/Dockerfile`."""
    project = tmp_path / "demo"
    config = project / ".pysolated"
    config.mkdir(parents=True)
    (config / "Dockerfile").write_text("FROM scratch\n", encoding="utf-8")
    monkeypatch.chdir(project)
    monkeypatch.setattr(_docker_module, "_resolve_host_uid", lambda: 1500)
    monkeypatch.setattr(_docker_module, "_resolve_host_gid", lambda: 1501)
    calls, fake = _fake_docker_subprocess_recorder()
    monkeypatch.setattr(_docker_module, "_stream_subprocess", fake)

    runner = CliRunner()
    result = runner.invoke(cli_module.app, ["docker", "build-image"])
    assert result.exit_code == 0, result.output
    argv = calls[0]
    i = argv.index("-f")
    assert argv[i + 1] == ".pysolated/Dockerfile"


def test_cli_docker_build_image_explicit_build_arg_overrides_auto(
    tmp_path: Path, monkeypatch: Any
) -> None:
    """An explicit `--build-arg AGENT_UID=…` overrides the host-UID auto-inject."""
    monkeypatch.chdir(tmp_path)
    monkeypatch.setattr(_docker_module, "_resolve_host_uid", lambda: 1500)
    monkeypatch.setattr(_docker_module, "_resolve_host_gid", lambda: 1501)
    calls, fake = _fake_docker_subprocess_recorder()
    monkeypatch.setattr(_docker_module, "_stream_subprocess", fake)

    runner = CliRunner()
    result = runner.invoke(
        cli_module.app,
        ["docker", "build-image", "--build-arg", "AGENT_UID=2000"],
    )
    assert result.exit_code == 0, result.output

    argv = calls[0]
    pairs = [argv[i + 1] for i, a in enumerate(argv) if a == "--build-arg"]
    # Explicit wins for the UID; the GID stays at the auto-injected host value.
    assert "AGENT_UID=2000" in pairs
    assert "AGENT_UID=1500" not in pairs
    assert "AGENT_GID=1501" in pairs


def test_cli_docker_build_image_passes_extra_build_args(
    tmp_path: Path, monkeypatch: Any
) -> None:
    """Arbitrary `--build-arg KEY=VALUE` pairs pass through to `docker build`."""
    monkeypatch.chdir(tmp_path)
    monkeypatch.setattr(_docker_module, "_resolve_host_uid", lambda: 1000)
    monkeypatch.setattr(_docker_module, "_resolve_host_gid", lambda: 1000)
    calls, fake = _fake_docker_subprocess_recorder()
    monkeypatch.setattr(_docker_module, "_stream_subprocess", fake)

    runner = CliRunner()
    result = runner.invoke(
        cli_module.app,
        [
            "docker",
            "build-image",
            "--build-arg",
            "FOO=bar",
            "--build-arg",
            "BAZ=qux",
        ],
    )
    assert result.exit_code == 0, result.output

    argv = calls[0]
    pairs = [argv[i + 1] for i, a in enumerate(argv) if a == "--build-arg"]
    assert "FOO=bar" in pairs
    assert "BAZ=qux" in pairs
    # Auto-injected values still present.
    assert "AGENT_UID=1000" in pairs
    assert "AGENT_GID=1000" in pairs


def test_cli_docker_build_image_malformed_build_arg_errors(
    tmp_path: Path, monkeypatch: Any
) -> None:
    """A `--build-arg` without `=` fails fast with a clear error and no docker call."""
    monkeypatch.chdir(tmp_path)
    calls, fake = _fake_docker_subprocess_recorder()
    monkeypatch.setattr(_docker_module, "_stream_subprocess", fake)

    runner = CliRunner()
    result = runner.invoke(
        cli_module.app,
        ["docker", "build-image", "--build-arg", "MISSING_EQUALS"],
    )
    assert result.exit_code == 2, result.output
    assert "--build-arg" in result.output
    assert "MISSING_EQUALS" in result.output
    assert calls == []


def test_cli_docker_build_image_empty_key_errors(
    tmp_path: Path, monkeypatch: Any
) -> None:
    """A `--build-arg =VALUE` (empty key) errors out before docker is invoked."""
    monkeypatch.chdir(tmp_path)
    calls, fake = _fake_docker_subprocess_recorder()
    monkeypatch.setattr(_docker_module, "_stream_subprocess", fake)

    runner = CliRunner()
    result = runner.invoke(
        cli_module.app,
        ["docker", "build-image", "--build-arg", "=value"],
    )
    assert result.exit_code == 2, result.output
    assert calls == []


def test_cli_docker_build_image_custom_file_flag(
    tmp_path: Path, monkeypatch: Any
) -> None:
    """`--file` overrides the Dockerfile path."""
    project = tmp_path / "demo"
    project.mkdir()
    monkeypatch.chdir(project)
    monkeypatch.setattr(_docker_module, "_resolve_host_uid", lambda: 1000)
    monkeypatch.setattr(_docker_module, "_resolve_host_gid", lambda: 1000)
    calls, fake = _fake_docker_subprocess_recorder()
    monkeypatch.setattr(_docker_module, "_stream_subprocess", fake)

    runner = CliRunner()
    result = runner.invoke(
        cli_module.app,
        ["docker", "build-image", "--file", "docker/Dev.Containerfile"],
    )
    assert result.exit_code == 0, result.output
    argv = calls[0]
    i = argv.index("-f")
    assert argv[i + 1] == "docker/Dev.Containerfile"


def test_cli_docker_build_image_explicit_tag(tmp_path: Path, monkeypatch: Any) -> None:
    """An explicit `--image` skips derivation."""
    monkeypatch.chdir(tmp_path)
    monkeypatch.setattr(_docker_module, "_resolve_host_uid", lambda: 1000)
    monkeypatch.setattr(_docker_module, "_resolve_host_gid", lambda: 1000)
    calls, fake = _fake_docker_subprocess_recorder()
    monkeypatch.setattr(_docker_module, "_stream_subprocess", fake)

    runner = CliRunner()
    result = runner.invoke(
        cli_module.app,
        ["docker", "build-image", "--image", "custom:tag"],
    )
    assert result.exit_code == 0, result.output
    argv = calls[0]
    i = argv.index("-t")
    assert argv[i + 1] == "custom:tag"


def test_cli_docker_remove_image_default_tag(tmp_path: Path, monkeypatch: Any) -> None:
    """`pysolated docker remove-image` runs `docker rmi <derived>` by default."""
    project = tmp_path / "demo"
    project.mkdir()
    monkeypatch.chdir(project)
    calls, fake = _fake_docker_subprocess_recorder()
    monkeypatch.setattr(_docker_module, "_stream_subprocess", fake)

    runner = CliRunner()
    result = runner.invoke(cli_module.app, ["docker", "remove-image"])
    assert result.exit_code == 0, result.output
    assert calls == [["docker", "rmi", "pysolated:demo"]]


def test_cli_docker_remove_image_explicit_tag(tmp_path: Path, monkeypatch: Any) -> None:
    monkeypatch.chdir(tmp_path)
    calls, fake = _fake_docker_subprocess_recorder()
    monkeypatch.setattr(_docker_module, "_stream_subprocess", fake)

    runner = CliRunner()
    result = runner.invoke(
        cli_module.app,
        ["docker", "remove-image", "--image", "custom:tag"],
    )
    assert result.exit_code == 0, result.output
    assert calls == [["docker", "rmi", "custom:tag"]]


def test_cli_docker_build_image_nonzero_exit_propagates(
    tmp_path: Path, monkeypatch: Any
) -> None:
    """A failing `docker build` surfaces a non-zero exit and the stderr."""
    from pysolated import ExecResult

    monkeypatch.chdir(tmp_path)
    monkeypatch.setattr(_docker_module, "_resolve_host_uid", lambda: 1000)
    monkeypatch.setattr(_docker_module, "_resolve_host_gid", lambda: 1000)

    async def failing(argv: list[str], **kwargs: Any) -> ExecResult:
        return ExecResult(exit_code=2, stdout="", stderr="syntax error")

    monkeypatch.setattr(_docker_module, "_stream_subprocess", failing)
    runner = CliRunner()
    result = runner.invoke(cli_module.app, ["docker", "build-image"])
    assert result.exit_code == 2, result.output
    assert "syntax error" in result.output
