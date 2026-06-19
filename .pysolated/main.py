import asyncio
import os
from pathlib import Path
from pysolated import run, claude_code
from pysolated.errors import IdleTimeoutError
from pysolated.sandboxes.podman import podman

PROMPT_FILE = Path(__file__).parent / "prompt.md"
REPO_ROOT = Path(__file__).parent.parent  # .pysolated/ lives at the repo root


def _require_env(name: str) -> str:
    """Read a required credential from the environment, failing fast if unset.

    Credentials must never be committed to this driver script — the sandbox
    has no access to the host environment, so they are read here and passed
    explicitly via the provider's `env=`.
    """
    value = os.environ.get(name)
    if not value:
        raise SystemExit(
            f"missing required environment variable {name!r}; "
            f"export it before running (e.g. `export {name}=...`)"
        )
    return value


## figure out HITL reviews
async def main():
    while True:
        try:
            result = await run(
                agent=claude_code("claude-opus-4-7"),
                sandbox=podman(
                    image="pysolated:pysolated",
                    env={
                        # MUST pass credentials explicitly — the sandbox does
                        # not inherit the host environment. Read from the host
                        # env here; never hard-code secrets in this file.
                        "CLAUDE_CODE_OAUTH_TOKEN": _require_env(
                            "CLAUDE_CODE_OAUTH_TOKEN"
                        ),
                        "GH_TOKEN": _require_env("GH_TOKEN"),
                    },
                ),
                prompt_file=str(PROMPT_FILE),
                prompt_args={"area": "auth"},
                cwd=str(
                    REPO_ROOT
                ),  # mount the repo root, not wherever main.py was launched from
                max_iterations=2,
                completion_signal=[
                    "<completion>ISSUE-DONE</completion>",
                    "<completion>NO-MORE-ISSUES</completion>",
                    "<completion>AWAITING-DEPENDENCIES</completion>",
                ],
                idle_timeout_seconds=600,
                completion_timeout_seconds=60,
            )
        except IdleTimeoutError as e:
            print(f"timed out: {e}")
            break

        print(result.text)
        print(result.branch, result.usage)
        if result.completion_signal == "<completion>NO-MORE-ISSUES</completion>":
            break
        if result.completion_signal == "<completion>AWAITING-DEPENDENCIES</completion>":
            print(
                "All outstanding tasks are blocked, awaiting unresolved dependencies."
            )
            answer = await asyncio.to_thread(
                input, "Confirm the dependencies are resolved to continue [y/N]: "
            )
            if answer.strip().lower() not in ("y", "yes"):
                print("Stopping until dependencies are resolved.")
                break


asyncio.run(main())
