"""Shared host-subprocess streamer.

Used by every sandbox provider to spawn a host subprocess and stream its
stdout line-by-line. Cancellation kills the process — that is how
`no_sandbox` honours abort and how the container providers kill their
`<engine> exec` clients mid-flight.
"""

from __future__ import annotations

import asyncio
from collections.abc import Mapping
from typing import Callable

from ..core import ExecResult

# Generous per-line limit for the subprocess stream reader. Claude's stream-json
# `system/init` and large assistant messages can exceed asyncio's 64 KiB default
# and would otherwise raise LimitOverrunError mid-stream.
_STREAM_LIMIT = 16 * 1024 * 1024


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
