"""Tests for the Podman sandbox provider with the podman CLI mocked.

Assertions target argv construction (`podman run`, `podman exec`, `podman rm
-f`, `podman image inspect`), error surfacing on a missing image, and the
factory/handle lifecycle promises borrowed from `no_sandbox`. No real `podman`
binary is required to run these tests.
"""

from __future__ import annotations

import asyncio
from collections.abc import AsyncIterator, Callable, Mapping
from contextlib import asynccontextmanager
from typing import Any
from unittest.mock import patch

import pytest

from pysolated import (
    ExecResult,
    Mount,
    PodmanImageNotFoundError,
    PodmanLaunchError,
    podman,
)
from pysolated.sandboxes import Podman, PodmanHandle, _build_volume_spec


# ---------------------------------------------------------------------------
# Test scaffolding — a stub for `_stream_subprocess`.
# ---------------------------------------------------------------------------


class _CLIStub:
    """Records every host subprocess call the sandbox makes.

    `responses` is the list of `ExecResult`s to return, one per call. If
    `responses` is shorter than the number of calls, the stub falls back to
    `default` (an exit-0 ok result) — handy for tests that only care about
    argv shape.
    """

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
    with patch("pysolated.sandboxes._stream_subprocess", stub):
        yield


# ---------------------------------------------------------------------------
# `create()` — preflight + `podman run` argv construction.
# ---------------------------------------------------------------------------


async def test_create_preflights_image_inspect() -> None:
    """`create()` runs `podman image inspect <image>` before `podman run`."""
    stub = _CLIStub()
    provider = podman(image="agent:latest")
    async with _patched(stub):
        handle = await provider.create(work_dir="/home/u/repo")
    assert stub.calls[0]["argv"] == ["podman", "image", "inspect", "agent:latest"]
    assert stub.calls[1]["argv"][:3] == ["podman", "run", "-d"]
    # And we got a real handle back.
    assert isinstance(handle, PodmanHandle)


async def test_create_raises_on_missing_image() -> None:
    """A non-zero `image inspect` raises `PodmanImageNotFoundError` with a clear message."""
    stub = _CLIStub(
        responses=[ExecResult(exit_code=125, stdout="", stderr="no such image")]
    )
    provider = podman(image="nope:latest")
    async with _patched(stub):
        with pytest.raises(PodmanImageNotFoundError) as exc:
            await provider.create(work_dir="/home/u/repo")
    msg = str(exc.value)
    assert "nope:latest" in msg
    assert "not found" in msg.lower()
    # `podman run` must not have been attempted after the preflight failed.
    assert len(stub.calls) == 1


async def test_create_raises_on_run_failure() -> None:
    """A non-zero `podman run` raises `PodmanLaunchError`, not a generic Exception."""
    stub = _CLIStub(
        responses=[
            ExecResult(exit_code=0, stdout="", stderr=""),  # inspect ok
            ExecResult(exit_code=125, stdout="", stderr="port already in use"),
        ]
    )
    provider = podman(image="agent:latest")
    async with _patched(stub):
        with pytest.raises(PodmanLaunchError) as exc:
            await provider.create(work_dir="/home/u/repo")
    assert "port already in use" in str(exc.value)


async def test_run_argv_has_keep_id_and_same_path_mount() -> None:
    """`podman run` uses `--user`, `--userns=keep-id:…`, and a same-path `:z` mount."""
    stub = _CLIStub()
    provider = podman(image="agent:latest")
    async with _patched(stub):
        await provider.create(work_dir="/home/u/repo")

    argv = stub.calls[1]["argv"]
    assert argv[:3] == ["podman", "run", "-d"]
    # --user 1000:1000
    i = argv.index("--user")
    assert argv[i + 1] == "1000:1000"
    # --userns=keep-id:uid=1000,gid=1000
    assert "--userns=keep-id:uid=1000,gid=1000" in argv
    # Same-path bind mount + `:z` SELinux label.
    j = argv.index("-v")
    assert argv[j + 1] == "/home/u/repo:/home/u/repo:z"
    # Detached `sleep infinity` against the requested image.
    assert argv[-4:] == ["--entrypoint", "sleep", "agent:latest", "infinity"]
    # Generated container name follows the pysolated-<uuid> convention.
    name = argv[argv.index("--name") + 1]
    assert name.startswith("pysolated-")
    assert len(name) > len("pysolated-")


async def test_run_argv_injects_home_and_provider_env_with_provider_winning() -> None:
    """`-e HOME=/home/agent` ships by default; provider env overrides it."""
    stub = _CLIStub()
    provider = podman(
        image="agent:latest",
        env={"ANTHROPIC_API_KEY": "sk-test", "HOME": "/workspace"},
    )
    async with _patched(stub):
        await provider.create(work_dir="/home/u/repo")

    argv = stub.calls[1]["argv"]
    env_pairs = [argv[i + 1] for i, a in enumerate(argv) if a == "-e"]
    # HOME default overridden by provider env (provider wins).
    assert "HOME=/workspace" in env_pairs
    assert "HOME=/home/agent" not in env_pairs
    assert "ANTHROPIC_API_KEY=sk-test" in env_pairs


async def test_run_argv_default_home_when_provider_omits_it() -> None:
    stub = _CLIStub()
    provider = podman(image="agent:latest")
    async with _patched(stub):
        await provider.create(work_dir="/home/u/repo")
    argv = stub.calls[1]["argv"]
    env_pairs = [argv[i + 1] for i, a in enumerate(argv) if a == "-e"]
    assert "HOME=/home/agent" in env_pairs


async def test_no_blanket_os_environ_forward(monkeypatch: pytest.MonkeyPatch) -> None:
    """A host env var not in provider env must not appear in the `-e` args."""
    monkeypatch.setenv("SECRET_HOST_VAR", "leaked")
    stub = _CLIStub()
    provider = podman(image="agent:latest")
    async with _patched(stub):
        await provider.create(work_dir="/home/u/repo")
    argv = stub.calls[1]["argv"]
    env_pairs = [argv[i + 1] for i, a in enumerate(argv) if a == "-e"]
    assert not any(p.startswith("SECRET_HOST_VAR=") for p in env_pairs), (
        "Podman provider must not forward host os.environ across the isolation boundary"
    )


async def test_run_argv_no_user_flags_when_userns_none() -> None:
    """When `userns=None` the `--user` pairing is dropped; podman defaults take over."""
    stub = _CLIStub()
    provider = podman(image="agent:latest", userns=None)
    async with _patched(stub):
        await provider.create(work_dir="/home/u/repo")
    argv = stub.calls[1]["argv"]
    assert "--user" not in argv
    assert not any(a.startswith("--userns=") for a in argv)


async def test_run_argv_custom_uid_gid() -> None:
    stub = _CLIStub()
    provider = podman(image="agent:latest", container_uid=1500, container_gid=1500)
    async with _patched(stub):
        await provider.create(work_dir="/home/u/repo")
    argv = stub.calls[1]["argv"]
    assert argv[argv.index("--user") + 1] == "1500:1500"
    assert "--userns=keep-id:uid=1500,gid=1500" in argv


async def test_run_argv_selinux_label_none_drops_z() -> None:
    """`selinux_label=None` produces a plain `host:sandbox` mount (no `:z`)."""
    stub = _CLIStub()
    provider = podman(image="agent:latest", selinux_label=None)
    async with _patched(stub):
        await provider.create(work_dir="/home/u/repo")
    argv = stub.calls[1]["argv"]
    j = argv.index("-v")
    assert argv[j + 1] == "/home/u/repo:/home/u/repo"


async def test_run_argv_selinux_label_capital_Z() -> None:
    stub = _CLIStub()
    provider = podman(image="agent:latest", selinux_label="Z")
    async with _patched(stub):
        await provider.create(work_dir="/home/u/repo")
    argv = stub.calls[1]["argv"]
    j = argv.index("-v")
    assert argv[j + 1] == "/home/u/repo:/home/u/repo:Z"


# ---------------------------------------------------------------------------
# `exec()` — `podman exec` argv construction.
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
    provider = podman(image="agent:latest")
    async with _patched(stub):
        handle = await provider.create(work_dir="/home/u/repo")
        await handle.exec(["git", "rev-parse", "HEAD"])

    argv = stub.calls[2]["argv"]
    # `podman exec <container> git rev-parse HEAD` — no `sh -c`.
    assert argv[0] == "podman"
    assert argv[1] == "exec"
    assert "-i" not in argv  # no stdin → no `-i`
    assert argv[-3:] == ["git", "rev-parse", "HEAD"]
    assert argv[-4] == handle.container_name
    assert "sh" not in argv
    assert "-c" not in argv


async def test_exec_argv_with_stdin_adds_dash_i() -> None:
    """A non-None `stdin` triggers `-i` so the host can pipe into the container."""
    stub = _CLIStub()
    provider = podman(image="agent:latest")
    async with _patched(stub):
        handle = await provider.create(work_dir="/home/u/repo")
        await handle.exec(["cat"], stdin="hello")
    argv = stub.calls[2]["argv"]
    assert argv[:3] == ["podman", "exec", "-i"]
    assert stub.calls[2]["stdin"] == "hello"


async def test_exec_argv_with_cwd_adds_dash_w() -> None:
    """`cwd=` becomes `-w <cwd>`, possible because of the same-path mount (ADR 0004)."""
    stub = _CLIStub()
    provider = podman(image="agent:latest")
    async with _patched(stub):
        handle = await provider.create(work_dir="/home/u/repo")
        await handle.exec(["pwd"], cwd="/home/u/repo/subdir")
    argv = stub.calls[2]["argv"]
    # `-w /home/u/repo/subdir <container> pwd`
    i = argv.index("-w")
    assert argv[i + 1] == "/home/u/repo/subdir"


async def test_exec_streams_via_on_line() -> None:
    """`on_line` is forwarded to the host subprocess streamer."""
    captured: list[str] = []

    async def fake_stream(
        argv: list[str],
        *,
        stdin: str | None = None,
        cwd: str | None = None,
        env: Mapping[str, str] | None = None,
        on_line: Callable[[str], None] | None = None,
    ) -> ExecResult:
        if argv[:2] == ["podman", "exec"]:
            assert on_line is not None
            on_line("line-one")
            on_line("line-two")
        return ExecResult(exit_code=0, stdout="line-one\nline-two", stderr="")

    with patch("pysolated.sandboxes._stream_subprocess", fake_stream):
        provider = podman(image="agent:latest")
        handle = await provider.create(work_dir="/home/u/repo")
        await handle.exec(["whatever"], on_line=captured.append)

    assert captured == ["line-one", "line-two"]


# ---------------------------------------------------------------------------
# `close()` — `podman rm -f` with idempotency.
# ---------------------------------------------------------------------------


async def test_close_runs_rm_f_and_is_idempotent() -> None:
    """`close()` runs `podman rm -f <container>` once; subsequent calls are no-ops."""
    stub = _CLIStub()
    provider = podman(image="agent:latest")
    async with _patched(stub):
        handle = await provider.create(work_dir="/home/u/repo")
        await handle.close()
        await handle.close()

    rm_calls = [c for c in stub.calls if c["argv"][:3] == ["podman", "rm", "-f"]]
    assert len(rm_calls) == 1
    assert rm_calls[0]["argv"][-1] == handle.container_name


async def test_close_swallows_rm_failure() -> None:
    """A failing `podman rm -f` must not raise — teardown is best-effort."""
    stub = _CLIStub(
        responses=[
            ExecResult(exit_code=0, stdout="", stderr=""),  # inspect
            ExecResult(exit_code=0, stdout="", stderr=""),  # run
            ExecResult(exit_code=1, stdout="", stderr="rm failed"),  # rm
        ]
    )
    provider = podman(image="agent:latest")
    async with _patched(stub):
        handle = await provider.create(work_dir="/home/u/repo")
        # Must not raise.
        await handle.close()


async def test_close_times_out_gracefully(monkeypatch: pytest.MonkeyPatch) -> None:
    """A stuck `podman rm -f` is bounded by the close timeout."""
    monkeypatch.setattr("pysolated.sandboxes._PODMAN_RM_TIMEOUT_SECONDS", 0.05)

    # Inspect + run return quickly; rm hangs.
    async def fake_stream(
        argv: list[str],
        *,
        stdin: str | None = None,
        cwd: str | None = None,
        env: Mapping[str, str] | None = None,
        on_line: Callable[[str], None] | None = None,
    ) -> ExecResult:
        if argv[:3] == ["podman", "rm", "-f"]:
            await asyncio.sleep(10)
        return ExecResult(exit_code=0, stdout="", stderr="")

    with patch("pysolated.sandboxes._stream_subprocess", fake_stream):
        provider = podman(image="agent:latest")
        handle = await provider.create(work_dir="/home/u/repo")
        # Must return within roughly the timeout, not block for 10s.
        await asyncio.wait_for(handle.close(), timeout=2.0)


# ---------------------------------------------------------------------------
# Provider + handle shape — frozen factory, fresh handle per create().
# ---------------------------------------------------------------------------


async def test_provider_is_a_factory_returning_handle() -> None:
    """`podman()` returns a frozen factory; `create()` yields a handle."""
    provider = podman(image="agent:latest")
    assert not hasattr(provider, "exec")
    stub = _CLIStub()
    async with _patched(stub):
        handle = await provider.create(work_dir="/home/u/repo")
    assert hasattr(handle, "exec")
    assert hasattr(handle, "close")


async def test_concurrent_creates_get_unique_container_names() -> None:
    """Each `create()` produces a fresh container name — concurrency safety guarantee."""
    provider = podman(image="agent:latest")
    stub = _CLIStub()
    async with _patched(stub):
        h1 = await provider.create(work_dir="/home/u/repo")
        h2 = await provider.create(work_dir="/home/u/repo")
    assert h1.container_name != h2.container_name


async def test_provider_is_frozen() -> None:
    provider = podman(image="agent:latest")
    with pytest.raises((AttributeError, Exception)):
        provider.image = "other"  # type: ignore[misc]


# ---------------------------------------------------------------------------
# Volume-spec builder — shared today by the repo mount, by issue #21 tomorrow.
# ---------------------------------------------------------------------------


def test_volume_spec_default_z_label() -> None:
    assert _build_volume_spec("/h", "/h", "z") == "/h:/h:z"


def test_volume_spec_readonly_and_label() -> None:
    """`ro` precedes the label so the option list reads as `…:ro,z`."""
    assert _build_volume_spec("/h", "/c", "z", readonly=True) == "/h:/c:ro,z"


def test_volume_spec_no_label_no_opts() -> None:
    assert _build_volume_spec("/h", "/h", None) == "/h:/h"


def test_volume_spec_capital_Z_label() -> None:
    assert _build_volume_spec("/h", "/h", "Z") == "/h:/h:Z"


def test_volume_spec_readonly_no_label() -> None:
    assert _build_volume_spec("/h", "/h", None, readonly=True) == "/h:/h:ro"


# ---------------------------------------------------------------------------
# Dataclass defaults — image-contract knobs.
# ---------------------------------------------------------------------------


def test_podman_defaults() -> None:
    """Default keep-id, uid/gid 1000, `:z`, `name='podman'`."""
    provider = podman(image="agent:latest")
    assert isinstance(provider, Podman)
    assert provider.image == "agent:latest"
    assert provider.userns == "keep-id"
    assert provider.container_uid == 1000
    assert provider.container_gid == 1000
    assert provider.selinux_label == "z"
    assert provider.name == "podman"
    assert provider.env == {}
    assert provider.mounts == []
    assert provider.cpus is None


# ---------------------------------------------------------------------------
# Custom mounts (issue #21) — argv composition + validation.
# ---------------------------------------------------------------------------


async def test_mounts_append_v_args_with_label(tmp_path: Any) -> None:
    """User mounts emit `-v host:sandbox:z` after the repo mount."""
    host = tmp_path / "creds"
    host.mkdir()
    stub = _CLIStub()
    provider = podman(
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
    provider = podman(
        image="agent:latest",
        mounts=[
            Mount(host_path=str(host), sandbox_path="/data", readonly=True),
        ],
    )
    async with _patched(stub):
        await provider.create(work_dir="/home/u/repo")
    argv = stub.calls[1]["argv"]
    v_args = [argv[i + 1] for i, a in enumerate(argv) if a == "-v"]
    assert v_args[1] == f"{host}:/data:ro,z"


async def test_mount_readonly_no_selinux_label(tmp_path: Any) -> None:
    """`selinux_label=None` + readonly produces `:ro` without a label suffix."""
    host = tmp_path / "data"
    host.mkdir()
    stub = _CLIStub()
    provider = podman(
        image="agent:latest",
        selinux_label=None,
        mounts=[Mount(host_path=str(host), sandbox_path="/data", readonly=True)],
    )
    async with _patched(stub):
        await provider.create(work_dir="/home/u/repo")
    argv = stub.calls[1]["argv"]
    v_args = [argv[i + 1] for i, a in enumerate(argv) if a == "-v"]
    assert v_args[1] == f"{host}:/data:ro"


async def test_mount_tilde_expands_against_host_home(
    tmp_path: Any, monkeypatch: pytest.MonkeyPatch
) -> None:
    """`~/foo` in `host_path` expands against the host `$HOME`."""
    fake_home = tmp_path / "home"
    fake_home.mkdir()
    (fake_home / "creds").mkdir()
    monkeypatch.setenv("HOME", str(fake_home))
    stub = _CLIStub()
    provider = podman(
        image="agent:latest",
        mounts=[Mount(host_path="~/creds", sandbox_path="/secrets")],
    )
    async with _patched(stub):
        await provider.create(work_dir="/home/u/repo")
    argv = stub.calls[1]["argv"]
    v_args = [argv[i + 1] for i, a in enumerate(argv) if a == "-v"]
    assert v_args[1].startswith(f"{fake_home}/creds:/secrets:")


async def test_mount_relative_host_path_resolved_against_cwd(
    tmp_path: Any, monkeypatch: pytest.MonkeyPatch
) -> None:
    """Relative `host_path` resolves against host cwd at create() time."""
    (tmp_path / "data").mkdir()
    monkeypatch.chdir(tmp_path)
    stub = _CLIStub()
    provider = podman(
        image="agent:latest",
        mounts=[Mount(host_path="data", sandbox_path="/data")],
    )
    async with _patched(stub):
        await provider.create(work_dir="/home/u/repo")
    argv = stub.calls[1]["argv"]
    v_args = [argv[i + 1] for i, a in enumerate(argv) if a == "-v"]
    assert v_args[1].startswith(f"{tmp_path}/data:/data:")


async def test_mount_missing_host_path_raises_before_run(tmp_path: Any) -> None:
    """Missing `host_path` fails fast with a clear message and no `podman run`."""
    stub = _CLIStub()
    provider = podman(
        image="agent:latest",
        mounts=[
            Mount(host_path=str(tmp_path / "missing"), sandbox_path="/data"),
        ],
    )
    async with _patched(stub):
        with pytest.raises(FileNotFoundError) as exc:
            await provider.create(work_dir="/home/u/repo")
    assert "does not exist" in str(exc.value)
    # Failed before `podman image inspect` or `podman run` ran.
    assert stub.calls == []


async def test_mount_relative_sandbox_path_rejected(tmp_path: Any) -> None:
    """A relative `sandbox_path` is rejected — silent dir-of-cwd surprises."""
    host = tmp_path / "data"
    host.mkdir()
    stub = _CLIStub()
    provider = podman(
        image="agent:latest",
        mounts=[Mount(host_path=str(host), sandbox_path="relative/path")],
    )
    async with _patched(stub):
        with pytest.raises(ValueError) as exc:
            await provider.create(work_dir="/home/u/repo")
    assert "absolute" in str(exc.value)


# ---------------------------------------------------------------------------
# `cpus` (issue #21) — `--cpus` argv emission.
# ---------------------------------------------------------------------------


async def test_cpus_emits_flag_when_set() -> None:
    stub = _CLIStub()
    provider = podman(image="agent:latest", cpus=1.5)
    async with _patched(stub):
        await provider.create(work_dir="/home/u/repo")
    argv = stub.calls[1]["argv"]
    i = argv.index("--cpus")
    assert argv[i + 1] == "1.5"


async def test_cpus_omitted_when_none() -> None:
    stub = _CLIStub()
    provider = podman(image="agent:latest")
    async with _patched(stub):
        await provider.create(work_dir="/home/u/repo")
    argv = stub.calls[1]["argv"]
    assert "--cpus" not in argv


async def test_cpus_integer_value_renders() -> None:
    stub = _CLIStub()
    provider = podman(image="agent:latest", cpus=2)
    async with _patched(stub):
        await provider.create(work_dir="/home/u/repo")
    argv = stub.calls[1]["argv"]
    i = argv.index("--cpus")
    assert argv[i + 1] == "2"
