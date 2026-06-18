"""Shared bind-mount value type + helpers.

Used by container providers to translate user-supplied `Mount`s into `-v`
arguments. The same volume-spec builder formats the orchestrator's same-path
repo mount (ADR 0004) and user-supplied mounts so SELinux and `ro` flags are
composed identically.
"""

from __future__ import annotations

import os
from dataclasses import dataclass
from typing import Literal

SELinuxLabel = Literal["z", "Z"] | None


@dataclass(frozen=True)
class Mount:
    """A user-supplied bind mount for a container provider.

    `host_path` is tilde-expanded against the host `$HOME` and, if relative,
    resolved against the host cwd at `create()` time; the resolved path must
    exist or `create()` fails fast. `sandbox_path` must be absolute — tilde
    expansion on the sandbox side would require knowing the in-container HOME
    and is deferred (see `docs/futures/features.md`). The sandbox-side parent
    directory must already exist in the image.

    `readonly=True` adds the `ro` option to the `-v` value; the provider's
    SELinux label is composed in alongside it (e.g. `…:ro,z`).
    """

    host_path: str
    sandbox_path: str
    readonly: bool = False


def _resolve_mount(mount: Mount) -> Mount:
    """Validate and resolve a `Mount` against the host filesystem.

    Tilde-expands `host_path` against `$HOME`, resolves relative paths against
    the host cwd, and raises `FileNotFoundError` if the resolved path is
    missing. `sandbox_path` must be absolute — relative or `~`-prefixed
    sandbox paths are rejected here because they would silently land somewhere
    surprising inside the container.
    """
    if not os.path.isabs(mount.sandbox_path):
        raise ValueError(
            f"Mount sandbox_path must be absolute (got {mount.sandbox_path!r})"
        )
    host_path = os.path.abspath(os.path.expanduser(mount.host_path))
    if not os.path.exists(host_path):
        raise FileNotFoundError(
            f"Mount host_path does not exist: {host_path!r} (from {mount.host_path!r})"
        )
    return Mount(
        host_path=host_path,
        sandbox_path=mount.sandbox_path,
        readonly=mount.readonly,
    )


def _build_volume_spec(
    host_path: str,
    sandbox_path: str,
    selinux_label: SELinuxLabel,
    *,
    readonly: bool = False,
) -> str:
    """Format a `-v` value with options composed in a stable order.

    Reused by the repo mount today and by user-supplied mounts: keeping the
    format in one place means `:ro,z` is always composed the same way no
    matter who supplies the mount.
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
