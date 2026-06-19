import asyncio
import os
from pathlib import Path
from dotenv import load_dotenv
from pysolated import run, codex
from pysolated.sandboxes import docker, Mount

# Load credentials from .pysolated/.env (gitignored) into the host environment
# so _require_env can read them. The .env is never mounted into the sandbox;
# the values are passed explicitly via the provider's `env=`.
load_dotenv(Path(__file__).parent / ".env")


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
        agent=codex("gpt-5.5", effort="high"),
        sandbox=docker(
            image="pysolated:pysolated",
            env={
                # MUST pass credentials explicitly — the sandbox does not
                # inherit the host environment. Never hard-code secrets here.
                "CLAUDE_CODE_OAUTH_TOKEN": _require_env("CLAUDE_CODE_OAUTH_TOKEN"),
                "GH_TOKEN": _require_env("GH_TOKEN"),
            },
            mounts=[
                Mount(
                    host_path=str(Path.home() / ".codex" / "auth.json"),
                    sandbox_path="/home/agent/.codex/auth.json",
                    readonly=True,
                ),
            ],
        ),
        prompt="say hi",
    )
    print(result.text)
    print(result.branch, result.usage)


asyncio.run(main())
