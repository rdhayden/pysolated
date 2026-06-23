"""The `pysolated init` scaffold core — pure `scaffold(repo_dir, options)`.

Mirrors Sandcastle's `scaffold()` vs `cli.ts` split. The CLI wizard layer in
`pysolated.cli` is a thin gap-filler on top: it gathers options (prompting
for any not given as flags, in a TTY) and then calls this `scaffold()`. The
tests target `scaffold()` directly; the wizard's all-flags path is the second
test surface.

This slice ships one agent × one sandbox — claude-code + podman. Each axis
is a registry entry; new agents/sandboxes land additively in the 2×2
widening (issue #46).

Per ADR 0011, the Containerfile is **composed** rather than shipped whole per
agent: a per-sandbox base carries `{{ROOT_INSTALL}}` (before `USER`) and
`{{USER_INSTALL}}` (after `USER`) slots filled by the per-agent install
snippet. Claude Code's `curl` installer targets `$HOME/.local/bin`, so its
snippet goes in the user slot; an agent that installs as root (codex —
landing in #46) would fill the root slot.

Per ADR 0012, the scaffolded driver uses the same `{{KEY}}` substitution as
the Containerfile (one uniform mechanism across every scaffolded file), and
forwards credentials generically via `dotenv_values(".pysolated/.env")` — the
driver is agent-agnostic; the per-agent `.env.example` block documents which
keys to set.
"""

from __future__ import annotations

from dataclasses import dataclass
from pathlib import Path

from .errors import PysolatedError
from .prompts import substitute_arguments


CONFIG_DIRNAME = ".pysolated"


class ScaffoldError(PysolatedError):
    """Base class for `scaffold()` failures."""


class ScaffoldExistsError(ScaffoldError):
    """`scaffold()` refuses to overwrite an existing `.pysolated/`."""


class UnknownAgentError(ScaffoldError):
    """The requested agent is not in the agent-install registry."""


class UnknownSandboxError(ScaffoldError):
    """The requested sandbox is not in the sandbox-base registry."""


@dataclass(frozen=True)
class ScaffoldOptions:
    """The choices `scaffold()` needs to compose a config directory.

    `agent` keys into the agent-install registry; `sandbox` keys into the
    sandbox-base registry; `model` is the agent's CLI-default model string.
    """

    agent: str
    sandbox: str
    model: str


@dataclass(frozen=True)
class _AgentInstall:
    """One agent registry entry.

    `import_name` is the symbol imported from `pysolated` (e.g. `claude_code`).
    `factory_call` is the agent constructor expression sans args
    (e.g. `claude_code` — the model string is filled by substitution into
    `claude_code("{{MODEL}}")`).
    `default_model` is the model string the CLI fills in when `--model` is
    omitted, mirroring the `--agent` default in `agents/_registry.py`.
    `root_install` is the Containerfile lines that run before `USER` (codex's
    root `npm i -g` lands here); empty for agents that install entirely as
    the unprivileged user.
    `user_install` is the Containerfile lines that run after `USER`
    (claude-code's curl installer lands here).
    `env_example` is the agent's credential block written into `.env.example`
    so the developer knows which keys to set.
    """

    import_name: str
    factory_call: str
    default_model: str
    root_install: str
    user_install: str
    env_example: str


@dataclass(frozen=True)
class _SandboxBase:
    """One sandbox registry entry.

    `import_name` is the symbol imported from `pysolated` (e.g. `podman`).
    `factory_call` is the sandbox constructor expression name (`podman`,
    `docker`). The scaffolded driver wraps it as
    `podman(env=dotenv_values(...))`.
    `containerfile_name` is the on-disk filename — `Containerfile` for
    podman, `Dockerfile` for docker (ADR 0011 — the file genuinely differs
    on both axes).
    `template` carries `{{ROOT_INSTALL}}` and `{{USER_INSTALL}}` slots that
    the agent install snippets fill.
    """

    import_name: str
    factory_call: str
    containerfile_name: str
    template: str


_CLAUDE_CODE = _AgentInstall(
    import_name="claude_code",
    factory_call="claude_code",
    default_model="claude-opus-4-7",
    root_install="",
    user_install="RUN curl -fsSL https://claude.ai/install.sh | bash\n",
    env_example=(
        "# Claude Code authenticates via an OAuth token; create one with\n"
        "# `claude /login` and paste it here.\n"
        "CLAUDE_CODE_OAUTH_TOKEN=\n"
    ),
)


_AGENT_INSTALLS: dict[str, _AgentInstall] = {
    "claude-code": _CLAUDE_CODE,
}


_PODMAN_BASE = _SandboxBase(
    import_name="podman",
    factory_call="podman",
    containerfile_name="Containerfile",
    template=(
        "FROM python:3.13-bookworm\n"
        "\n"
        "RUN apt-get update && apt-get install -y \\\n"
        "  git curl jq \\\n"
        "  && rm -rf /var/lib/apt/lists/*\n"
        "\n"
        "RUN addgroup --gid 1000 agent \\\n"
        " && adduser --uid 1000 --gid 1000 --home /home/agent agent\n"
        "\n"
        "{{ROOT_INSTALL}}"
        'ENV PATH="/home/agent/.local/bin:$PATH"\n'
        "\n"
        "USER agent\n"
        "\n"
        "{{USER_INSTALL}}"
        "WORKDIR /home/agent\n"
        "\n"
        'ENTRYPOINT ["sleep", "infinity"]\n'
    ),
)


_SANDBOX_BASES: dict[str, _SandboxBase] = {
    "podman": _PODMAN_BASE,
}


# The driver template — non-runnable on its own (ADR 0012); tests parse the
# scaffolded *output* with `ast.parse`. The `{{KEY}}` placeholders are filled
# by `substitute_arguments`, the same engine used for the Containerfile.
_DRIVER_TEMPLATE = '''"""Scaffolded driver — edit to taste.

Drives the agent inside the sandbox via `pysolated.run()`. Credentials are
loaded from `.pysolated/.env` (gitignored) and passed explicitly to the
sandbox provider; the sandbox does not inherit the host environment.
"""

from __future__ import annotations

import asyncio
from pathlib import Path

from dotenv import dotenv_values

from pysolated import run, {{AGENT_IMPORT}}, {{SANDBOX_IMPORT}}

CONFIG_DIR = Path(__file__).parent
PROMPT_FILE = CONFIG_DIR / "prompt.md"
ENV_FILE = CONFIG_DIR / ".env"
REPO_ROOT = CONFIG_DIR.parent


async def main() -> None:
    result = await run(
        agent={{AGENT_FACTORY}}("{{MODEL}}"),
        sandbox={{SANDBOX_FACTORY}}(env=dotenv_values(str(ENV_FILE))),
        prompt_file=str(PROMPT_FILE),
        cwd=str(REPO_ROOT),
    )
    print(result.text)


if __name__ == "__main__":
    asyncio.run(main())
'''


_PROMPT_SKELETON = (
    "# Prompt\n"
    "\n"
    "Describe the task you want the agent to perform. This file is a\n"
    "**prompt template** — `{{KEY}}` placeholders are substituted from\n"
    "`prompt_args` passed to `run()`, and `` !`command` `` markers are\n"
    "expanded by running the command in the sandbox.\n"
)


_GITIGNORE = ".env\n"


def agent_names() -> list[str]:
    """Registered agent names, in insertion order — for CLI error messages."""
    return list(_AGENT_INSTALLS)


def sandbox_names() -> list[str]:
    """Registered sandbox names, in insertion order — for CLI error messages."""
    return list(_SANDBOX_BASES)


def default_model_for(agent: str) -> str:
    """The CLI's `--model` default for an agent. Raises if the agent is unknown."""
    entry = _AGENT_INSTALLS.get(agent)
    if entry is None:
        valid = ", ".join(_AGENT_INSTALLS)
        raise UnknownAgentError(f"unknown agent {agent!r}. Valid agents: {valid}.")
    return entry.default_model


def scaffold(repo_dir: Path | str, options: ScaffoldOptions) -> None:
    """Write the `.pysolated/` config directory into `repo_dir`.

    Raises:
        ScaffoldExistsError: `repo_dir/.pysolated` already exists; nothing is
            written. The directory's existing contents are preserved.
        UnknownAgentError: `options.agent` is not in the agent-install registry.
        UnknownSandboxError: `options.sandbox` is not in the sandbox-base registry.
    """
    repo = Path(repo_dir)
    config_dir = repo / CONFIG_DIRNAME
    if config_dir.exists():
        raise ScaffoldExistsError(
            f"{CONFIG_DIRNAME}/ already exists at {config_dir} — "
            "remove it or scaffold elsewhere"
        )

    agent = _AGENT_INSTALLS.get(options.agent)
    if agent is None:
        valid = ", ".join(_AGENT_INSTALLS)
        raise UnknownAgentError(
            f"unknown agent {options.agent!r}. Valid agents: {valid}."
        )
    sandbox = _SANDBOX_BASES.get(options.sandbox)
    if sandbox is None:
        valid = ", ".join(_SANDBOX_BASES)
        raise UnknownSandboxError(
            f"unknown sandbox {options.sandbox!r}. Valid sandboxes: {valid}."
        )

    driver = substitute_arguments(
        _DRIVER_TEMPLATE,
        user_args={},
        built_in_args={
            "AGENT_IMPORT": agent.import_name,
            "AGENT_FACTORY": agent.factory_call,
            "SANDBOX_IMPORT": sandbox.import_name,
            "SANDBOX_FACTORY": sandbox.factory_call,
            "MODEL": options.model,
        },
    )
    containerfile = substitute_arguments(
        sandbox.template,
        user_args={},
        built_in_args={
            "ROOT_INSTALL": agent.root_install,
            "USER_INSTALL": agent.user_install,
        },
    )

    config_dir.mkdir(parents=False, exist_ok=False)
    (config_dir / "main.py").write_text(driver, encoding="utf-8")
    (config_dir / "prompt.md").write_text(_PROMPT_SKELETON, encoding="utf-8")
    (config_dir / sandbox.containerfile_name).write_text(
        containerfile, encoding="utf-8"
    )
    (config_dir / ".gitignore").write_text(_GITIGNORE, encoding="utf-8")
    (config_dir / ".env.example").write_text(agent.env_example, encoding="utf-8")


__all__ = [
    "CONFIG_DIRNAME",
    "ScaffoldError",
    "ScaffoldExistsError",
    "ScaffoldOptions",
    "UnknownAgentError",
    "UnknownSandboxError",
    "agent_names",
    "default_model_for",
    "sandbox_names",
    "scaffold",
]
