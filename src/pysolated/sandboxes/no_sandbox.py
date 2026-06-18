"""The no-sandbox provider — runs the agent directly on the host.

`close()` is a no-op (there is no container or VM to tear down) but it exists
so the orchestrator's lifecycle code path is identical across providers.
"""

from __future__ import annotations

import os
from dataclasses import dataclass, field
from typing import Callable

from ..core import ExecResult
from ._streaming import _stream_subprocess


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
