"""Sandbox providers.

v1 ships two providers under the factory+handle seam (ADR 0003):

- `no_sandbox` — no isolation; the agent runs directly on the host. `close()`
  is a no-op.
- `podman` — a long-lived rootless Podman container as the isolation boundary.
  `create()` preflights the image, starts a detached `sleep infinity` container
  with `--userns=keep-id` + same-path repo bind mount (ADR 0004), and `close()`
  removes it. `exec()` is argv passthrough through `podman exec` — no `sh -c`
  wrapper (ADR 0001).

Shared leaf helpers (`_streaming`, `_mounts`, `_images`) live in private
sibling modules so a second container provider can reuse them without
extracting a base class.
"""

from __future__ import annotations

from ._mounts import Mount, SELinuxLabel
from .no_sandbox import NoSandbox, NoSandboxHandle, no_sandbox
from .podman import (
    Podman,
    PodmanHandle,
    PodmanImageNotFoundError,
    PodmanLaunchError,
    UserNamespace,
    build_image,
    podman,
    remove_image,
)

__all__ = [
    # No-sandbox provider.
    "NoSandbox",
    "NoSandboxHandle",
    "no_sandbox",
    # Podman provider.
    "Podman",
    "PodmanHandle",
    "podman",
    "PodmanImageNotFoundError",
    "PodmanLaunchError",
    "UserNamespace",
    "build_image",
    "remove_image",
    # Mounts.
    "Mount",
    "SELinuxLabel",
]
