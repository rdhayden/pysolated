"""The Docker sandbox provider — a long-lived container, sibling to Podman.

`create()` preflights the image, starts a detached `sleep infinity` container
with an always-on `--user N:N` + same-path repo bind mount (ADR 0004), and
`close()` removes it. `exec()` is argv passthrough through `docker exec` — no
`sh -c` wrapper (ADR 0001).

Docker has no `--userns=keep-id`, so host ↔ container file ownership is
aligned via the host UID baked into the image (`pysolated docker build-image`,
issue #27) plus an always-on `--user` flag — `userns` is not a knob here. The
UID-match pre-flight that fails loudly on a mismatched image lands in #26;
this slice ships against a user-provided image that already lines up (e.g.
host UID 1000 against an image built with `AGENT_UID=1000`).

The Docker image contract is heavier than Podman's. The Containerfile must:

```dockerfile
ARG AGENT_UID=1000
ARG AGENT_GID=1000
RUN groupmod -o -g $AGENT_GID <user> && \\
    usermod -o -u $AGENT_UID -g $AGENT_GID -d /home/agent -m -l agent <user>
USER ${AGENT_UID}:${AGENT_GID}
```

The `-o` flag lets alignment succeed when the host UID/GID collides with one
already in the base image; the numeric `USER` is what makes the (forthcoming)
pre-flight `{{.Config.User}}` check parseable. `git` + the agent CLI on
`PATH`, writable `HOME=/home/agent`.
"""

from __future__ import annotations

import asyncio
import os
import uuid
from dataclasses import dataclass, field
from typing import Callable

from ..core import ExecResult
from ._images import _derive_default_image_name
from ._mounts import Mount, SELinuxLabel, _build_volume_spec, _resolve_mount
from ._streaming import _stream_subprocess

# Best-effort timeout on `docker rm -f` from close(). Teardown must not block
# the run's real outcome on a stuck docker client.
_DOCKER_RM_TIMEOUT_SECONDS = 10.0


class DockerImageNotFoundError(RuntimeError):
    """Raised at `create()` when `docker image inspect <image>` fails.

    A separate type so callers can distinguish "image not built/pulled yet"
    from generic Docker launch failures.
    """


class DockerImageUidMismatchError(RuntimeError):
    """Raised at `create()` when the image's baked-in UID disagrees with `container_uid`.

    Docker has no `--userns=keep-id`, so a UID mismatch means silent `EACCES`
    on image-built paths (e.g. `/home/agent`, the agent CLI). The pre-flight
    fails loudly with both remedies named — rebuild with
    `pysolated docker build-image`, or pass `container_uid=<image-uid>` to
    match the image (ADR 0005).
    """


class DockerLaunchError(RuntimeError):
    """Raised when `docker run` fails to start the container."""


def _check_image_user(
    inspect: ExecResult,
    *,
    image: str,
    expected_uid: int,
) -> None:
    """Decide whether the image's `Config.User` matches `expected_uid`.

    Pure — takes the result of
    `docker image inspect <image> --format '{{.Config.User}}'` and either
    raises or returns. Cases:

    - inspect failed (non-zero exit) → `DockerImageNotFoundError`.
    - empty `User` (no `USER` directive) or non-numeric `User`
      (e.g. `USER agent`) → return; the check is skipped silently because the
      contract is documented and `{{.Config.User}}` can't be compared.
    - numeric leading UID matches → return.
    - numeric leading UID disagrees → `DockerImageUidMismatchError`, message
      naming both remedies.

    The check is UID-only — `{{.Config.User}}` often omits the GID and a GID
    mismatch rarely causes the `EACCES`-on-binaries failure this guards.
    """
    if inspect.exit_code != 0:
        raise DockerImageNotFoundError(
            f"Docker image not found: {image!r}. "
            f"Build it with `pysolated docker build-image` "
            f"(or `docker pull {image}` if you have a remote tag)."
        )
    user = inspect.stdout.strip()
    if not user:
        return
    head = user.split(":", 1)[0]
    if not head.isdigit():
        return
    image_uid = int(head)
    if image_uid == expected_uid:
        return
    raise DockerImageUidMismatchError(
        f"Docker image {image!r} was built with UID {image_uid}, but "
        f"`container_uid={expected_uid}`. Rebuild the image with "
        f"`pysolated docker build-image` to bake in your host UID, or "
        f"pass `container_uid={image_uid}` to `docker(...)` to match the image."
    )


def _resolve_host_uid() -> int:
    """Host UID, falling back to 1000 where `os.getuid` is unavailable.

    The fallback keeps construction non-crashing on Windows; full Windows
    support (mount normalization, gitdir remapping) is deferred — see
    `docs/futures/platform-correctness.md`.
    """
    getuid = getattr(os, "getuid", None)
    return getuid() if getuid is not None else 1000


def _resolve_host_gid() -> int:
    """Host GID, falling back to 1000 where `os.getgid` is unavailable."""
    getgid = getattr(os, "getgid", None)
    return getgid() if getgid is not None else 1000


@dataclass
class DockerHandle:
    """The live Docker handle wrapping a long-lived container."""

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
        cmd: list[str] = ["docker", "exec"]
        if stdin is not None:
            cmd.append("-i")
        if cwd is not None:
            cmd.extend(["-w", cwd])
        cmd.append(self.container_name)
        cmd.extend(argv)
        return await _stream_subprocess(cmd, stdin=stdin, on_line=on_line)

    async def close(self) -> None:
        if self._closed:
            return
        self._closed = True
        try:
            await asyncio.wait_for(
                _stream_subprocess(["docker", "rm", "-f", self.container_name]),
                timeout=_DOCKER_RM_TIMEOUT_SECONDS,
            )
        except (TimeoutError, asyncio.TimeoutError, Exception):  # noqa: BLE001
            pass


@dataclass(frozen=True)
class Docker:
    """The Docker sandbox provider — a long-lived container, sibling to Podman.

    Build via `docker(image=…)`. Frozen and reusable; each `create()` yields a
    fresh container with a unique name so the same provider can drive
    concurrent runs.

    Defaults `container_uid`/`container_gid` to the host UID/GID (resolved in
    the `docker()` factory). `--user N:N` is always emitted — there is no
    `userns` field and no opt-out, because Docker's only ownership lever is
    `--user` and disabling it only reintroduces the `EACCES` it prevents
    (ADR 0005).
    """

    image: str
    container_uid: int
    container_gid: int
    env: dict[str, str] = field(default_factory=dict)
    name: str = "docker"
    selinux_label: SELinuxLabel = "z"
    mounts: list[Mount] = field(default_factory=list)
    cpus: float | None = None

    async def create(self, work_dir: str) -> DockerHandle:
        resolved_mounts = [_resolve_mount(m) for m in self.mounts]

        inspect = await _stream_subprocess(
            [
                "docker",
                "image",
                "inspect",
                self.image,
                "--format",
                "{{.Config.User}}",
            ]
        )
        _check_image_user(
            inspect,
            image=self.image,
            expected_uid=self.container_uid,
        )

        container_name = f"pysolated-{uuid.uuid4().hex[:12]}"
        run_argv = self._build_run_argv(
            container_name=container_name,
            work_dir=work_dir,
            resolved_mounts=resolved_mounts,
        )
        result = await _stream_subprocess(run_argv)
        if result.exit_code != 0:
            raise DockerLaunchError(
                f"Failed to start Docker container {container_name!r} "
                f"(exit {result.exit_code}): {result.stderr.strip()}"
            )
        return DockerHandle(container_name=container_name)

    def _build_run_argv(
        self,
        *,
        container_name: str,
        work_dir: str,
        resolved_mounts: list[Mount],
    ) -> list[str]:
        """Construct the `docker run` argv. Pure — unit-tested directly."""
        argv: list[str] = ["docker", "run", "-d", "--name", container_name]

        argv.extend(["--user", f"{self.container_uid}:{self.container_gid}"])

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

        # Provider env wins over the HOME default.
        merged_env = {"HOME": "/home/agent", **self.env}
        for key, value in merged_env.items():
            argv.extend(["-e", f"{key}={value}"])

        argv.extend(["--entrypoint", "sleep", self.image, "infinity"])
        return argv


def docker(
    *,
    image: str | None = None,
    env: dict[str, str] | None = None,
    container_uid: int | None = None,
    container_gid: int | None = None,
    selinux_label: SELinuxLabel = "z",
    mounts: list[Mount] | None = None,
    cpus: float | None = None,
) -> Docker:
    """Create a Docker sandbox provider.

    `image` defaults to `pysolated:<sanitized-host-dirname>` (matching what
    `pysolated docker build-image` produces). `container_uid`/`container_gid`
    default to the host UID/GID — Docker has no `keep-id`, so the `--user`
    value must match the *host* UID for bind-mounted repo files to be
    writable; the host UID is the only correct default. `env` is injected at
    `docker run` and wins over the `HOME=/home/agent` default. The container
    is not given the host's `os.environ`: pass credentials explicitly via
    `env=` or by mounting a file.
    """
    return Docker(
        image=image if image is not None else _derive_default_image_name(),
        env=env or {},
        container_uid=container_uid
        if container_uid is not None
        else _resolve_host_uid(),
        container_gid=container_gid
        if container_gid is not None
        else _resolve_host_gid(),
        selinux_label=selinux_label,
        mounts=list(mounts) if mounts is not None else [],
        cpus=cpus,
    )
