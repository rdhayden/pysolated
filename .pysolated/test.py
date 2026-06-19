import asyncio
import os
from pysolated import run, claude_code
from pysolated.sandboxes import docker


def _require_env(name: str) -> str:
    """Read a required credential from the environment, failing fast if unset.

    Credentials must never be committed — the sandbox has no access to the host
    environment, so they are read here and passed explicitly via `env=`.
    """
    value = os.environ.get(name)
    if not value:
        raise SystemExit(
            f"missing required environment variable {name!r}; "
            f"export it before running (e.g. `export {name}=...`)"
        )
    return value


async def main():
    result = await run(
        agent=claude_code("claude-opus-4-7"),
        sandbox=docker(
            image="pysolated:pysolated",
            env={
                # MUST pass credentials explicitly — the sandbox does not
                # inherit the host environment. Never hard-code secrets here.
                "CLAUDE_CODE_OAUTH_TOKEN": _require_env("CLAUDE_CODE_OAUTH_TOKEN"),
                "GH_TOKEN": _require_env("GH_TOKEN"),
            },
        ),
        prompt="say hi",
    )
    print(result.text)
    print(result.branch, result.usage)


asyncio.run(main())
