"""Tests for the Docker sandbox provider with the docker CLI mocked.

Sibling to `tests/test_podman.py`: same `_CLIStub` pattern asserts `docker run`,
`docker exec`, `docker rm -f`, and `docker image inspect` argv; the Docker
provider diverges from Podman on host-UID defaults, an always-on `--user` (no
`userns` field), and a heavier image contract. No real `docker` binary is
required to run these tests.
"""

from __future__ import annotations

import asyncio
import sys
from collections.abc import AsyncIterator, Callable, Mapping
from contextlib import asynccontextmanager
from typing import Any
from unittest.mock import patch

import pytest

from pysolated import (
    DockerImageNotFoundError,
    DockerLaunchError,
    ExecResult,
    Mount,
    docker,
)
import pysolated.sandboxes.docker as _docker_module_import  # noqa: F401

from pysolated.sandboxes import Docker, DockerHandle

# `from .docker import docker` in `sandboxes/__init__.py` shadows the submodule
# binding the same way it does for podman; resolve the submodule from
# sys.modules so tests can patch its helpers in place.
_docker_module = sys.modules["pysolated.sandboxes.docker"]


# ---------------------------------------------------------------------------
# Test scaffolding — a stub for `_stream_subprocess`.
# ---------------------------------------------------------------------------


class _CLIStub:
    """Records every host subprocess call the sandbox makes."""

    def __init__(
        self,
        *,
        responses: list[ExecResult] | None = None,
        default: ExecResult | None = None,
    ) -> None:
        self.calls: list[dict[str, Any]] = []
        self._responses = list(responses or [])
        self._default = default or ExecResult(exit_code=0, stdout="", stderr="")

    async def __call__(
        self,
        argv: list[str],
        *,
        stdin: str | None = None,
        cwd: str | None = None,
        env: Mapping[str, str] | None = None,
        on_line: Callable[[str], None] | None = None,
    ) -> ExecResult:
        self.calls.append(
            {
                "argv": list(argv),
                "stdin": stdin,
                "cwd": cwd,
                "env": dict(env) if env is not None else None,
                "on_line": on_line,
            }
        )
        if self._responses:
            return self._responses.pop(0)
        return self._default


@asynccontextmanager
async def _patched(stub: _CLIStub) -> AsyncIterator[None]:
    with patch.object(_docker_module, "_stream_subprocess", stub):
        yield


# ---------------------------------------------------------------------------
# Defaults — host UID/GID, no `userns` field, image derived from cwd.
# ---------------------------------------------------------------------------


def test_docker_defaults(monkeypatch: pytest.MonkeyPatch) -> None:
    """Default container_uid/gid = host UID/GID, `:z`, `name='docker'`, no userns field."""
    monkeypatch.setattr(_docker_module, "_resolve_host_uid", lambda: 1234)
    monkeypatch.setattr(_docker_module, "_resolve_host_gid", lambda: 5678)

    provider = docker(image="agent:latest")
    assert isinstance(provider, Docker)
    assert provider.image == "agent:latest"
    assert provider.container_uid == 1234
    assert provider.container_gid == 5678
    assert provider.selinux_label == "z"
    assert provider.name == "docker"
    assert provider.env == {}
    assert provider.mounts == []
    assert provider.cpus is None
    # Diverges from Podman: there is no `userns` field, no opt-out.
    assert not hasattr(provider, "userns")


def test_host_uid_fallback_to_1000(monkeypatch: pytest.MonkeyPatch) -> None:
    """When `os.getuid`/`os.getgid` are unavailable, fall back to 1000."""
    monkeypatch.delattr(_docker_module.os, "getuid", raising=False)
    monkeypatch.delattr(_docker_module.os, "getgid", raising=False)
    provider = docker(image="agent:latest")
    assert provider.container_uid == 1000
    assert provider.container_gid == 1000


def test_explicit_uid_gid_overrides_host_default() -> None:
    provider = docker(image="agent:latest", container_uid=2000, container_gid=2001)
    assert provider.container_uid == 2000
    assert provider.container_gid == 2001


# ---------------------------------------------------------------------------
# `create()` — existence preflight + `docker run` argv construction.
# ---------------------------------------------------------------------------


async def test_create_preflights_image_inspect() -> None:
    """`create()` runs `docker image inspect <image>` before `docker run`."""
    stub = _CLIStub()
    provider = docker(image="agent:latest")
    async with _patched(stub):
        handle = await provider.create(work_dir="/home/u/repo")
    assert stub.calls[0]["argv"] == ["docker", "image", "inspect", "agent:latest"]
    assert stub.calls[1]["argv"][:3] == ["docker", "run", "-d"]
    assert isinstance(handle, DockerHandle)


async def test_create_raises_on_missing_image() -> None:
    """A non-zero `image inspect` raises `DockerImageNotFoundError`."""
    stub = _CLIStub(
        responses=[ExecResult(exit_code=1, stdout="", stderr="No such image")]
    )
    provider = docker(image="nope:latest")
    async with _patched(stub):
        with pytest.raises(DockerImageNotFoundError) as exc:
            await provider.create(work_dir="/home/u/repo")
    msg = str(exc.value)
    assert "nope:latest" in msg
    assert "not found" in msg.lower()
    assert "pysolated docker build-image" in msg
    # `docker run` must not have been attempted after the preflight failed.
    assert len(stub.calls) == 1


async def test_create_raises_on_run_failure() -> None:
    """A non-zero `docker run` raises `DockerLaunchError`."""
    stub = _CLIStub(
        responses=[
            ExecResult(exit_code=0, stdout="", stderr=""),  # inspect ok
            ExecResult(exit_code=125, stdout="", stderr="port already in use"),
        ]
    )
    provider = docker(image="agent:latest")
    async with _patched(stub):
        with pytest.raises(DockerLaunchError) as exc:
            await provider.create(work_dir="/home/u/repo")
    assert "port already in use" in str(exc.value)


async def test_run_argv_has_always_on_user_and_same_path_mount(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """`docker run` emits `--user host:host`, a same-path `:z` mount, and NO `--userns=`."""
    monkeypatch.setattr(_docker_module, "_resolve_host_uid", lambda: 1500)
    monkeypatch.setattr(_docker_module, "_resolve_host_gid", lambda: 1501)
    stub = _CLIStub()
    provider = docker(image="agent:latest")
    async with _patched(stub):
        await provider.create(work_dir="/home/u/repo")

    argv = stub.calls[1]["argv"]
    assert argv[:3] == ["docker", "run", "-d"]
    # --user uses host UID/GID by default.
    i = argv.index("--user")
    assert argv[i + 1] == "1500:1501"
    # No `--userns=` flag at all — Docker has no keep-id equivalent.
    assert not any(a.startswith("--userns") for a in argv)
    # Same-path bind mount + `:z` SELinux label (ADR 0004).
    j = argv.index("-v")
    assert argv[j + 1] == "/home/u/repo:/home/u/repo:z"
    # Detached `sleep infinity` against the requested image.
    assert argv[-4:] == ["--entrypoint", "sleep", "agent:latest", "infinity"]
    # Generated container name follows the pysolated-<uuid> convention.
    name = argv[argv.index("--name") + 1]
    assert name.startswith("pysolated-")
    assert len(name) > len("pysolated-")


async def test_run_argv_user_always_emitted_with_explicit_uid() -> None:
    """`--user N:N` is always emitted — no `userns=None` opt-out exists."""
    stub = _CLIStub()
    provider = docker(image="agent:latest", container_uid=2000, container_gid=2001)
    async with _patched(stub):
        await provider.create(work_dir="/home/u/repo")
    argv = stub.calls[1]["argv"]
    assert argv[argv.index("--user") + 1] == "2000:2001"
    assert not any(a.startswith("--userns") for a in argv)


async def test_run_argv_injects_home_and_provider_env_with_provider_winning() -> None:
    """`-e HOME=/home/agent` ships by default; provider env overrides it."""
    stub = _CLIStub()
    provider = docker(
        image="agent:latest",
        env={"ANTHROPIC_API_KEY": "sk-test", "HOME": "/workspace"},
    )
    async with _patched(stub):
        await provider.create(work_dir="/home/u/repo")
    argv = stub.calls[1]["argv"]
    env_pairs = [argv[i + 1] for i, a in enumerate(argv) if a == "-e"]
    assert "HOME=/workspace" in env_pairs
    assert "HOME=/home/agent" not in env_pairs
    assert "ANTHROPIC_API_KEY=sk-test" in env_pairs


async def test_run_argv_default_home_when_provider_omits_it() -> None:
    stub = _CLIStub()
    provider = docker(image="agent:latest")
    async with _patched(stub):
        await provider.create(work_dir="/home/u/repo")
    argv = stub.calls[1]["argv"]
    env_pairs = [argv[i + 1] for i, a in enumerate(argv) if a == "-e"]
    assert "HOME=/home/agent" in env_pairs


async def test_no_blanket_os_environ_forward(monkeypatch: pytest.MonkeyPatch) -> None:
    """A host env var not in provider env must not appear in the `-e` args."""
    monkeypatch.setenv("SECRET_HOST_VAR", "leaked")
    stub = _CLIStub()
    provider = docker(image="agent:latest")
    async with _patched(stub):
        await provider.create(work_dir="/home/u/repo")
    argv = stub.calls[1]["argv"]
    env_pairs = [argv[i + 1] for i, a in enumerate(argv) if a == "-e"]
    assert not any(p.startswith("SECRET_HOST_VAR=") for p in env_pairs), (
        "Docker provider must not forward host os.environ across the isolation boundary"
    )


async def test_run_argv_selinux_label_none_drops_z() -> None:
    """`selinux_label=None` produces a plain `host:sandbox` mount (no `:z`)."""
    stub = _CLIStub()
    provider = docker(image="agent:latest", selinux_label=None)
    async with _patched(stub):
        await provider.create(work_dir="/home/u/repo")
    argv = stub.calls[1]["argv"]
    j = argv.index("-v")
    assert argv[j + 1] == "/home/u/repo:/home/u/repo"


async def test_run_argv_selinux_label_capital_Z() -> None:
    stub = _CLIStub()
    provider = docker(image="agent:latest", selinux_label="Z")
    async with _patched(stub):
        await provider.create(work_dir="/home/u/repo")
    argv = stub.calls[1]["argv"]
    j = argv.index("-v")
    assert argv[j + 1] == "/home/u/repo:/home/u/repo:Z"


# ---------------------------------------------------------------------------
# `exec()` — `docker exec` argv construction.
# ---------------------------------------------------------------------------


async def test_exec_argv_passthrough_no_sh_c_wrapper() -> None:
    """`exec()` splices argv verbatim — no `sh -c` wrapping (ADR 0001)."""
    stub = _CLIStub(
        responses=[
            ExecResult(exit_code=0, stdout="", stderr=""),  # inspect
            ExecResult(exit_code=0, stdout="", stderr=""),  # run
            ExecResult(exit_code=0, stdout="ok", stderr=""),
        ]
    )
    provider = docker(image="agent:latest")
    async with _patched(stub):
        handle = await provider.create(work_dir="/home/u/repo")
        await handle.exec(["git", "rev-parse", "HEAD"])

    argv = stub.calls[2]["argv"]
    assert argv[0] == "docker"
    assert argv[1] == "exec"
    assert "-i" not in argv  # no stdin → no `-i`
    assert argv[-3:] == ["git", "rev-parse", "HEAD"]
    assert argv[-4] == handle.container_name
    assert "sh" not in argv
    assert "-c" not in argv


async def test_exec_argv_with_stdin_adds_dash_i() -> None:
    stub = _CLIStub()
    provider = docker(image="agent:latest")
    async with _patched(stub):
        handle = await provider.create(work_dir="/home/u/repo")
        await handle.exec(["cat"], stdin="hello")
    argv = stub.calls[2]["argv"]
    assert argv[:3] == ["docker", "exec", "-i"]
    assert stub.calls[2]["stdin"] == "hello"


async def test_exec_argv_with_cwd_adds_dash_w() -> None:
    """`cwd=` becomes `-w <cwd>` (possible because of the same-path mount, ADR 0004)."""
    stub = _CLIStub()
    provider = docker(image="agent:latest")
    async with _patched(stub):
        handle = await provider.create(work_dir="/home/u/repo")
        await handle.exec(["pwd"], cwd="/home/u/repo/subdir")
    argv = stub.calls[2]["argv"]
    i = argv.index("-w")
    assert argv[i + 1] == "/home/u/repo/subdir"


async def test_exec_streams_via_on_line() -> None:
    captured: list[str] = []

    async def fake_stream(
        argv: list[str],
        *,
        stdin: str | None = None,
        cwd: str | None = None,
        env: Mapping[str, str] | None = None,
        on_line: Callable[[str], None] | None = None,
    ) -> ExecResult:
        if argv[:2] == ["docker", "exec"]:
            assert on_line is not None
            on_line("line-one")
            on_line("line-two")
        return ExecResult(exit_code=0, stdout="line-one\nline-two", stderr="")

    with patch.object(_docker_module, "_stream_subprocess", fake_stream):
        provider = docker(image="agent:latest")
        handle = await provider.create(work_dir="/home/u/repo")
        await handle.exec(["whatever"], on_line=captured.append)

    assert captured == ["line-one", "line-two"]


# ---------------------------------------------------------------------------
# `close()` — `docker rm -f` with idempotency.
# ---------------------------------------------------------------------------


async def test_close_runs_rm_f_and_is_idempotent() -> None:
    """`close()` runs `docker rm -f <container>` once; subsequent calls are no-ops."""
    stub = _CLIStub()
    provider = docker(image="agent:latest")
    async with _patched(stub):
        handle = await provider.create(work_dir="/home/u/repo")
        await handle.close()
        await handle.close()

    rm_calls = [c for c in stub.calls if c["argv"][:3] == ["docker", "rm", "-f"]]
    assert len(rm_calls) == 1
    assert rm_calls[0]["argv"][-1] == handle.container_name


async def test_close_swallows_rm_failure() -> None:
    """A failing `docker rm -f` must not raise — teardown is best-effort."""
    stub = _CLIStub(
        responses=[
            ExecResult(exit_code=0, stdout="", stderr=""),  # inspect
            ExecResult(exit_code=0, stdout="", stderr=""),  # run
            ExecResult(exit_code=1, stdout="", stderr="rm failed"),  # rm
        ]
    )
    provider = docker(image="agent:latest")
    async with _patched(stub):
        handle = await provider.create(work_dir="/home/u/repo")
        await handle.close()  # must not raise


async def test_close_times_out_gracefully(monkeypatch: pytest.MonkeyPatch) -> None:
    """A stuck `docker rm -f` is bounded by the close timeout."""
    monkeypatch.setattr(_docker_module, "_DOCKER_RM_TIMEOUT_SECONDS", 0.05)

    async def fake_stream(
        argv: list[str],
        *,
        stdin: str | None = None,
        cwd: str | None = None,
        env: Mapping[str, str] | None = None,
        on_line: Callable[[str], None] | None = None,
    ) -> ExecResult:
        if argv[:3] == ["docker", "rm", "-f"]:
            await asyncio.sleep(10)
        return ExecResult(exit_code=0, stdout="", stderr="")

    with patch.object(_docker_module, "_stream_subprocess", fake_stream):
        provider = docker(image="agent:latest")
        handle = await provider.create(work_dir="/home/u/repo")
        await asyncio.wait_for(handle.close(), timeout=2.0)


# ---------------------------------------------------------------------------
# Provider + handle shape — frozen factory, fresh handle per create().
# ---------------------------------------------------------------------------


async def test_provider_is_a_factory_returning_handle() -> None:
    """`docker()` returns a frozen factory; `create()` yields a handle."""
    provider = docker(image="agent:latest")
    assert not hasattr(provider, "exec")
    stub = _CLIStub()
    async with _patched(stub):
        handle = await provider.create(work_dir="/home/u/repo")
    assert hasattr(handle, "exec")
    assert hasattr(handle, "close")


async def test_concurrent_creates_get_unique_container_names() -> None:
    """Each `create()` produces a fresh container name — concurrency safety guarantee."""
    provider = docker(image="agent:latest")
    stub = _CLIStub()
    async with _patched(stub):
        h1 = await provider.create(work_dir="/home/u/repo")
        h2 = await provider.create(work_dir="/home/u/repo")
    assert h1.container_name != h2.container_name


def test_provider_is_frozen() -> None:
    provider = docker(image="agent:latest")
    with pytest.raises((AttributeError, Exception)):
        provider.image = "other"  # type: ignore[misc]


# ---------------------------------------------------------------------------
# Custom mounts — argv composition + validation (mirrors Podman).
# ---------------------------------------------------------------------------


async def test_mounts_append_v_args_with_label(tmp_path: Any) -> None:
    """User mounts emit `-v host:sandbox:z` after the repo mount."""
    host = tmp_path / "creds"
    host.mkdir()
    stub = _CLIStub()
    provider = docker(
        image="agent:latest",
        mounts=[Mount(host_path=str(host), sandbox_path="/secrets")],
    )
    async with _patched(stub):
        await provider.create(work_dir="/home/u/repo")
    argv = stub.calls[1]["argv"]
    v_args = [argv[i + 1] for i, a in enumerate(argv) if a == "-v"]
    assert v_args[0] == "/home/u/repo:/home/u/repo:z"
    assert v_args[1] == f"{host}:/secrets:z"


async def test_mount_readonly_composes_ro_and_label(tmp_path: Any) -> None:
    """`readonly=True` composes `:ro,z` via the shared volume-spec builder."""
    host = tmp_path / "data"
    host.mkdir()
    stub = _CLIStub()
    provider = docker(
        image="agent:latest",
        mounts=[Mount(host_path=str(host), sandbox_path="/data", readonly=True)],
    )
    async with _patched(stub):
        await provider.create(work_dir="/home/u/repo")
    argv = stub.calls[1]["argv"]
    v_args = [argv[i + 1] for i, a in enumerate(argv) if a == "-v"]
    assert v_args[1] == f"{host}:/data:ro,z"


async def test_mount_missing_host_path_raises_before_run(tmp_path: Any) -> None:
    """Missing `host_path` fails fast with a clear message and no `docker run`."""
    stub = _CLIStub()
    provider = docker(
        image="agent:latest",
        mounts=[
            Mount(host_path=str(tmp_path / "missing"), sandbox_path="/data"),
        ],
    )
    async with _patched(stub):
        with pytest.raises(FileNotFoundError) as exc:
            await provider.create(work_dir="/home/u/repo")
    assert "does not exist" in str(exc.value)
    assert stub.calls == []


# ---------------------------------------------------------------------------
# `cpus` — `--cpus` argv emission.
# ---------------------------------------------------------------------------


async def test_cpus_emits_flag_when_set() -> None:
    stub = _CLIStub()
    provider = docker(image="agent:latest", cpus=1.5)
    async with _patched(stub):
        await provider.create(work_dir="/home/u/repo")
    argv = stub.calls[1]["argv"]
    i = argv.index("--cpus")
    assert argv[i + 1] == "1.5"


async def test_cpus_omitted_when_none() -> None:
    stub = _CLIStub()
    provider = docker(image="agent:latest")
    async with _patched(stub):
        await provider.create(work_dir="/home/u/repo")
    argv = stub.calls[1]["argv"]
    assert "--cpus" not in argv


# ---------------------------------------------------------------------------
# Default image name derivation.
# ---------------------------------------------------------------------------


def test_docker_image_defaults_to_derived_when_omitted(
    tmp_path: Any, monkeypatch: pytest.MonkeyPatch
) -> None:
    """`docker()` with no `image=` derives `pysolated:<cwd-dirname>`."""
    project = tmp_path / "demo"
    project.mkdir()
    monkeypatch.chdir(project)
    provider = docker()
    assert provider.image == "pysolated:demo"


def test_docker_explicit_image_still_wins() -> None:
    """An explicit `image=` is used verbatim — derivation only fills the gap."""
    provider = docker(image="custom:tag")
    assert provider.image == "custom:tag"
