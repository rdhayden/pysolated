"""Sandbox providers.

v1 ships two providers under the factory+handle seam (ADR 0003):

- `no_sandbox` — no isolation; the agent runs directly on the host. `close()`
  is a no-op.
- `podman` — a long-lived rootless Podman container as the isolation boundary.
  `create()` preflights the image, starts a detached `sleep infinity` container
  with `--userns=keep-id` + same-path repo bind mount (ADR 0004), and `close()`
  removes it. `exec()` is argv passthrough through `podman exec` — no `sh -c`
  wrapper (ADR 0001).
"""

from __future__ import annotations

import asyncio
import os
import uuid
from collections.abc import Mapping
from dataclasses import dataclass, field
from typing import Callable, Literal

from .core import ExecResult

# Generous per-line limit for the subprocess stream reader. Claude's stream-json
# `system/init` and large assistant messages can exceed asyncio's 64 KiB default
# and would otherwise raise LimitOverrunError mid-stream.
_STREAM_LIMIT = 16 * 1024 * 1024

# Best-effort timeout on `podman rm -f` from close(). Teardown must not block
# the run's real outcome on a stuck podman client.
_PODMAN_RM_TIMEOUT_SECONDS = 10.0


async def _stream_subprocess(
    argv: list[str],
    *,
    stdin: str | None = None,
    cwd: str | None = None,
    env: Mapping[str, str] | None = None,
    on_line: Callable[[str], None] | None = None,
) -> ExecResult:
    """Spawn argv as a host subprocess, streaming stdout line-by-line.

    Returns exit code + full stdout + full stderr. Kills the subprocess if the
    awaiting task is cancelled — this is how `no_sandbox` honours abort and
    how the Podman provider kills the host `podman exec` client mid-flight.

    When `env` is `None`, the subprocess inherits the host environment; this is
    what the Podman provider wants for its `podman` client invocations
    (whatever auth the host shell already has). The no-sandbox handle passes
    the merged `{os.environ, provider.env}` so provider env wins.
    """
    proc = await asyncio.create_subprocess_exec(
        *argv,
        stdin=asyncio.subprocess.PIPE
        if stdin is not None
        else asyncio.subprocess.DEVNULL,
        stdout=asyncio.subprocess.PIPE,
        stderr=asyncio.subprocess.PIPE,
        cwd=cwd,
        env=dict(env) if env is not None else None,
        limit=_STREAM_LIMIT,
    )

    stdout_lines: list[str] = []
    stderr_chunks: list[str] = []

    async def feed_stdin() -> None:
        if stdin is None or proc.stdin is None:
            return
        proc.stdin.write(stdin.encode("utf-8"))
        await proc.stdin.drain()
        proc.stdin.close()

    async def pump_stdout() -> None:
        assert proc.stdout is not None
        async for raw in proc.stdout:
            line = raw.decode("utf-8", "replace").rstrip("\r\n")
            stdout_lines.append(line)
            if on_line is not None:
                on_line(line)

    async def pump_stderr() -> None:
        assert proc.stderr is not None
        data = await proc.stderr.read()
        if data:
            stderr_chunks.append(data.decode("utf-8", "replace"))

    try:
        await asyncio.gather(feed_stdin(), pump_stdout(), pump_stderr())
        exit_code = await proc.wait()
    except asyncio.CancelledError:
        proc.kill()
        await proc.wait()
        raise
    finally:
        if proc.returncode is None:
            proc.kill()
            await proc.wait()

    return ExecResult(
        exit_code=exit_code,
        stdout="\n".join(stdout_lines),
        stderr="".join(stderr_chunks),
    )


# ---------------------------------------------------------------------------
# No-sandbox provider.
# ---------------------------------------------------------------------------


@dataclass
class NoSandboxHandle:
    """The live no-sandbox handle: runs commands as host subprocesses.

    Created by `NoSandbox.create()`. `close()` is a no-op — there is no
    container or VM to tear down — but it exists so the orchestrator's
    lifecycle code path is identical across providers.
    """

    env: dict[str, str] = field(default_factory=dict)
    _closed: bool = False

    async def exec(
        self,
        argv: list[str],
        *,
        stdin: str | None = None,
        cwd: str | None = None,
        on_line: Callable[[str], None] | None = None,
    ) -> ExecResult:
        """Spawn a host subprocess, streaming stdout line-by-line via `on_line`.

        Returns exit code, full stdout, and full stderr. Kills the subprocess if
        the awaiting task is cancelled.
        """
        return await _stream_subprocess(
            argv,
            stdin=stdin,
            cwd=cwd,
            env={**os.environ, **self.env},
            on_line=on_line,
        )

    async def close(self) -> None:
        """No-op: nothing to tear down on the host.

        Idempotent — the orchestrator's `finally` and the `atexit` backstop
        may both call this on the same handle.
        """
        self._closed = True


@dataclass(frozen=True)
class NoSandbox:
    """The no-sandbox provider — runs the agent directly on the host.

    Build via `no_sandbox()`. Frozen and reusable; each `create()` returns a
    fresh handle, so the same provider can drive concurrent runs.
    """

    env: dict[str, str] = field(default_factory=dict)
    name: str = "no-sandbox"

    async def create(self, work_dir: str) -> NoSandboxHandle:
        """Return a fresh handle. `work_dir` is unused — the host has no setup."""
        return NoSandboxHandle(env=dict(self.env))


def no_sandbox(*, env: dict[str, str] | None = None) -> NoSandbox:
    """Create a no-sandbox provider — the agent runs directly on the host."""
    return NoSandbox(env=env or {})


# ---------------------------------------------------------------------------
# Podman provider.
# ---------------------------------------------------------------------------


class PodmanImageNotFoundError(RuntimeError):
    """Raised at `create()` when `podman image inspect <image>` fails.

    A separate type so callers can distinguish "image not built/pulled yet"
    from generic Podman launch failures.
    """


class PodmanLaunchError(RuntimeError):
    """Raised when `podman run` fails to start the container."""


UserNamespace = Literal["keep-id"] | None
SELinuxLabel = Literal["z", "Z"] | None


@dataclass
class PodmanHandle:
    """The live Podman handle wrapping a long-lived rootless container.

    `exec()` is argv-passthrough through `podman exec` — no `sh -c` wrapper, no
    quoting (ADR 0001). Cancellation kills the host `podman exec` client; the
    in-container process is reaped by `podman rm -f` from `close()`, which is
    the true kill switch (the `atexit` backstop catches abnormal exits).
    """

    container_name: str
    _closed: bool = False

    async def exec(
        self,
        argv: list[str],
        *,
        stdin: str | None = None,
        cwd: str | None = None,
        on_line: Callable[[str], None] | None = None,
    ) -> ExecResult:
        """Run `argv` inside the container via `podman exec`.

        `-i` is added when `stdin` is supplied so the host can pipe the prompt
        in; `-w <cwd>` is added when `cwd` is supplied (the same-path bind
        mount means `cwd` is a path the container recognises unchanged —
        ADR 0004).
        """
        cmd: list[str] = ["podman", "exec"]
        if stdin is not None:
            cmd.append("-i")
        if cwd is not None:
            cmd.extend(["-w", cwd])
        cmd.append(self.container_name)
        cmd.extend(argv)
        return await _stream_subprocess(cmd, stdin=stdin, on_line=on_line)

    async def close(self) -> None:
        """Remove the container with `podman rm -f`. Idempotent + best-effort.

        Wrapped in a short timeout so a stuck Podman client can't block the
        run's real outcome. The `atexit` backstop and the orchestrator's
        `finally` may both call this on the same handle — calling twice is a
        no-op after the first removal.
        """
        if self._closed:
            return
        self._closed = True
        try:
            await asyncio.wait_for(
                _stream_subprocess(["podman", "rm", "-f", self.container_name]),
                timeout=_PODMAN_RM_TIMEOUT_SECONDS,
            )
        except (TimeoutError, asyncio.TimeoutError, Exception):  # noqa: BLE001
            # Best-effort: the run's outcome (return value or exception) must
            # not be masked by a teardown failure. SIGKILL leaks during
            # abnormal interpreter shutdown are documented in
            # docs/futures/features.md (no global signal-handler registry).
            pass


@dataclass(frozen=True)
class Podman:
    """The Podman sandbox provider — a long-lived rootless container.

    Build via `podman(image=…)`. Frozen and reusable; each `create()` yields a
    fresh container with a unique name so the same provider can drive
    concurrent runs.

    The image contract:

    - A user/group exists at `container_uid`:`container_gid` (default 1000:1000)
      so `--userns=keep-id:uid=N,gid=N` + `--user N:N` map host ↔ container
      ownership without a chown step (ADR 0004).
    - `git` and the configured agent CLI are on `PATH` inside the image.
    - The user has a writable `HOME` (defaults to `/home/agent`; override via
      `env={"HOME": …}`).

    Environment is injected with `-e` at `podman run`: `HOME=/home/agent` plus
    provider `env` (provider wins). There is **no** blanket `os.environ`
    forward across the isolation boundary — agent credentials (e.g.
    `ANTHROPIC_API_KEY`) must be passed explicitly via provider `env` or a
    mounted file. This is a deliberate divergence from `no_sandbox` and is
    where the isolation actually shows up at the API surface.
    """

    image: str
    env: dict[str, str] = field(default_factory=dict)
    name: str = "podman"
    userns: UserNamespace = "keep-id"
    container_uid: int = 1000
    container_gid: int = 1000
    selinux_label: SELinuxLabel = "z"

    async def create(self, work_dir: str) -> PodmanHandle:
        """Preflight the image and start a detached `sleep infinity` container.

        Raises `PodmanImageNotFoundError` if `podman image inspect <image>`
        fails — fail fast with a clear message rather than letting `podman run`
        produce a less obvious error a few lines later. Raises
        `PodmanLaunchError` if `podman run` itself fails.

        The repo is bind-mounted at its host path inside the container
        (ADR 0004); the orchestrator's `cwd=` then passes straight through
        `podman exec -w` unchanged.
        """
        inspect = await _stream_subprocess(["podman", "image", "inspect", self.image])
        if inspect.exit_code != 0:
            raise PodmanImageNotFoundError(
                f"Podman image not found: {self.image!r}. "
                f"Build or pull it before running "
                f"(e.g. `podman pull {self.image}` or build from a Containerfile)."
            )

        container_name = f"pysolated-{uuid.uuid4().hex[:12]}"
        run_argv = self._build_run_argv(
            container_name=container_name, work_dir=work_dir
        )
        result = await _stream_subprocess(run_argv)
        if result.exit_code != 0:
            raise PodmanLaunchError(
                f"Failed to start Podman container {container_name!r} "
                f"(exit {result.exit_code}): {result.stderr.strip()}"
            )
        return PodmanHandle(container_name=container_name)

    def _build_run_argv(self, *, container_name: str, work_dir: str) -> list[str]:
        """Construct the `podman run` argv. Pure — unit-tested directly."""
        argv: list[str] = ["podman", "run", "-d", "--name", container_name]

        if self.userns == "keep-id":
            argv.extend(
                [
                    "--user",
                    f"{self.container_uid}:{self.container_gid}",
                    f"--userns=keep-id:uid={self.container_uid},gid={self.container_gid}",
                ]
            )

        argv.extend(["-v", _build_volume_spec(work_dir, work_dir, self.selinux_label)])

        # Provider env wins over the HOME default: dict|merge with provider
        # entries last means a user-supplied HOME overrides /home/agent.
        merged_env = {"HOME": "/home/agent", **self.env}
        for key, value in merged_env.items():
            argv.extend(["-e", f"{key}={value}"])

        argv.extend(["--entrypoint", "sleep", self.image, "infinity"])
        return argv


def _build_volume_spec(
    host_path: str,
    sandbox_path: str,
    selinux_label: SELinuxLabel,
    *,
    readonly: bool = False,
) -> str:
    """Format a `-v` value with options composed in a stable order.

    Reused by the repo mount today and by user-supplied mounts (issue #21):
    keeping the format in one place means `:ro,z` is always composed the same
    way no matter who supplies the mount.
    """
    opts: list[str] = []
    if readonly:
        opts.append("ro")
    if selinux_label is not None:
        opts.append(selinux_label)
    spec = f"{host_path}:{sandbox_path}"
    if opts:
        spec += ":" + ",".join(opts)
    return spec


def podman(
    *,
    image: str,
    env: dict[str, str] | None = None,
    userns: UserNamespace = "keep-id",
    container_uid: int = 1000,
    container_gid: int = 1000,
    selinux_label: SELinuxLabel = "z",
) -> Podman:
    """Create a Podman sandbox provider.

    `image` is required — its contract (user at uid/gid, `git` + agent CLI on
    `PATH`, writable `HOME`) is documented on `Podman`. `env` is injected at
    `podman run` time and wins over the `HOME=/home/agent` default. The
    container is not given the host's `os.environ`: pass credentials
    explicitly via `env=` or by mounting a file (custom mounts ship in a
    later slice).
    """
    return Podman(
        image=image,
        env=env or {},
        userns=userns,
        container_uid=container_uid,
        container_gid=container_gid,
        selinux_label=selinux_label,
    )
