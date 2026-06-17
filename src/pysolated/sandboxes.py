"""Sandbox providers.

v1 ships one: `no_sandbox`, which creates no sandbox at all — the agent runs
directly on the host in the working directory. No isolation, despite the
project name (real isolation is the next slice).
"""

from __future__ import annotations

import asyncio
import os
from dataclasses import dataclass, field
from typing import Callable

from .core import ExecResult

# Generous per-line limit for the subprocess stream reader. Claude's stream-json
# `system/init` and large assistant messages can exceed asyncio's 64 KiB default
# and would otherwise raise LimitOverrunError mid-stream.
_STREAM_LIMIT = 16 * 1024 * 1024


@dataclass(frozen=True)
class NoSandbox:
    """Runs commands directly on the host. Build via `no_sandbox()`."""

    env: dict[str, str] = field(default_factory=dict)
    name: str = "no-sandbox"

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
        proc = await asyncio.create_subprocess_exec(
            *argv,
            stdin=asyncio.subprocess.PIPE
            if stdin is not None
            else asyncio.subprocess.DEVNULL,
            stdout=asyncio.subprocess.PIPE,
            stderr=asyncio.subprocess.PIPE,
            cwd=cwd,
            env={**os.environ, **self.env},
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


def no_sandbox(*, env: dict[str, str] | None = None) -> NoSandbox:
    """Create a no-sandbox provider — the agent runs directly on the host."""
    return NoSandbox(env=env or {})
