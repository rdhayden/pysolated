"""The Podman sandbox provider — a long-lived rootless container.

`create()` preflights the image, starts a detached `sleep infinity` container
with `--userns=keep-id` + same-path repo bind mount (ADR 0004), and `close()`
removes it. `exec()` is argv passthrough through `podman exec` — no `sh -c`
wrapper (ADR 0001).
"""

from __future__ import annotations

import asyncio
import os
import uuid
from dataclasses import dataclass, field
from typing import Callable, Literal

from ..core import ExecResult
from ._images import _derive_default_image_name
from ._mounts import Mount, SELinuxLabel, _build_volume_spec, _resolve_mount
from ._streaming import _stream_subprocess

# Best-effort timeout on `podman rm -f` from close(). Teardown must not block
# the run's real outcome on a stuck podman client.
_PODMAN_RM_TIMEOUT_SECONDS = 10.0


class PodmanImageNotFoundError(RuntimeError):
    """Raised at `create()` when `podman image inspect <image>` fails.

    A separate type so callers can distinguish "image not built/pulled yet"
    from generic Podman launch failures.
    """


class PodmanLaunchError(RuntimeError):
    """Raised when `podman run` fails to start the container."""


UserNamespace = Literal["keep-id"] | None


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
    mounts: list[Mount] = field(default_factory=list)
    cpus: float | None = None

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
        resolved_mounts = [_resolve_mount(m) for m in self.mounts]

        inspect = await _stream_subprocess(["podman", "image", "inspect", self.image])
        if inspect.exit_code != 0:
            raise PodmanImageNotFoundError(
                f"Podman image not found: {self.image!r}. "
                f"Build it with `pysolated podman build-image` "
                f"(or `podman pull {self.image}` if you have a remote tag)."
            )

        container_name = f"pysolated-{uuid.uuid4().hex[:12]}"
        run_argv = self._build_run_argv(
            container_name=container_name,
            work_dir=work_dir,
            resolved_mounts=resolved_mounts,
        )
        result = await _stream_subprocess(run_argv)
        if result.exit_code != 0:
            raise PodmanLaunchError(
                f"Failed to start Podman container {container_name!r} "
                f"(exit {result.exit_code}): {result.stderr.strip()}"
            )
        return PodmanHandle(container_name=container_name)

    def _build_run_argv(
        self,
        *,
        container_name: str,
        work_dir: str,
        resolved_mounts: list[Mount],
    ) -> list[str]:
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

        if self.cpus is not None:
            argv.extend(["--cpus", str(self.cpus)])

        argv.extend(["-v", _build_volume_spec(work_dir, work_dir, self.selinux_label)])

        for mount in resolved_mounts:
            argv.extend(
                [
                    "-v",
                    _build_volume_spec(
                        mount.host_path,
                        mount.sandbox_path,
                        self.selinux_label,
                        readonly=mount.readonly,
                    ),
                ]
            )

        # Provider env wins over the HOME default: dict|merge with provider
        # entries last means a user-supplied HOME overrides /home/agent.
        merged_env = {"HOME": "/home/agent", **self.env}
        for key, value in merged_env.items():
            argv.extend(["-e", f"{key}={value}"])

        argv.extend(["--entrypoint", "sleep", self.image, "infinity"])
        return argv


async def build_image(
    image: str,
    *,
    containerfile: str = "Containerfile",
    context: str | None = None,
) -> ExecResult:
    """Run `podman build -f <containerfile> -t <image> <context>`.

    `context` defaults to the host cwd at call time — the same directory whose
    name `_derive_default_image_name()` would sanitize, so a no-arg
    `pysolated podman build-image` from a repo root is fully self-describing.
    """
    ctx = context if context is not None else os.getcwd()
    return await _stream_subprocess(
        ["podman", "build", "-f", containerfile, "-t", image, ctx]
    )


async def remove_image(image: str) -> ExecResult:
    """Run `podman rmi <image>`."""
    return await _stream_subprocess(["podman", "rmi", image])


def podman(
    *,
    image: str | None = None,
    env: dict[str, str] | None = None,
    userns: UserNamespace = "keep-id",
    container_uid: int = 1000,
    container_gid: int = 1000,
    selinux_label: SELinuxLabel = "z",
    mounts: list[Mount] | None = None,
    cpus: float | None = None,
) -> Podman:
    """Create a Podman sandbox provider.

    `image` defaults to `pysolated:<sanitized-host-dirname>` (see
    `_derive_default_image_name`) so callers can rely on the same name the
    `pysolated podman build-image` CLI produces. Its contract (user at
    uid/gid, `git` + agent CLI on `PATH`, writable `HOME`) is documented on
    `Podman`. `env` is injected at `podman run` time and wins over the
    `HOME=/home/agent` default. The container is not given the host's
    `os.environ`: pass credentials explicitly via `env=` or by mounting a file.

    `mounts` add user bind mounts on top of the same-path repo mount; each is
    composed through the same volume-spec builder as the repo mount, so the
    SELinux label and `ro` flag are formatted identically. `cpus` (fractional
    ok) becomes `--cpus N`; omitted when `None`.
    """
    return Podman(
        image=image if image is not None else _derive_default_image_name(),
        env=env or {},
        userns=userns,
        container_uid=container_uid,
        container_gid=container_gid,
        selinux_label=selinux_label,
        mounts=list(mounts) if mounts is not None else [],
        cpus=cpus,
    )
