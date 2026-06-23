"""Tests for `init`'s pure `scaffold(repo_dir, options)` core.

The wizard shell is tested separately through the CLI's all-flags path.
These tests cover the scaffold core directly: file layout, Containerfile
composition (per-sandbox base + per-agent install snippets), driver
substitution, and the "fail if `.pysolated/` already exists" guard.
"""

from __future__ import annotations

import ast
from pathlib import Path

import pytest

from pysolated.init import ScaffoldExistsError, ScaffoldOptions, scaffold


def _claude_code_podman(model: str = "claude-opus-4-7") -> ScaffoldOptions:
    return ScaffoldOptions(agent="claude-code", sandbox="podman", model=model)


def _codex_podman(model: str = "gpt-5-codex") -> ScaffoldOptions:
    return ScaffoldOptions(agent="codex", sandbox="podman", model=model)


def _claude_code_docker(model: str = "claude-opus-4-7") -> ScaffoldOptions:
    return ScaffoldOptions(agent="claude-code", sandbox="docker", model=model)


def _codex_docker(model: str = "gpt-5-codex") -> ScaffoldOptions:
    return ScaffoldOptions(agent="codex", sandbox="docker", model=model)


def test_scaffold_creates_config_directory_with_all_five_files(tmp_path: Path) -> None:
    scaffold(tmp_path, _claude_code_podman())

    config = tmp_path / ".pysolated"
    assert (config / "main.py").is_file()
    assert (config / "prompt.md").is_file()
    assert (config / "Containerfile").is_file()
    assert (config / ".gitignore").is_file()
    assert (config / ".env.example").is_file()


def test_scaffold_fails_if_config_directory_already_exists(tmp_path: Path) -> None:
    (tmp_path / ".pysolated").mkdir()

    with pytest.raises(ScaffoldExistsError):
        scaffold(tmp_path, _claude_code_podman())


def test_scaffold_writes_nothing_when_config_directory_already_exists(
    tmp_path: Path,
) -> None:
    existing = tmp_path / ".pysolated"
    existing.mkdir()
    (existing / "sentinel.txt").write_text("preserved")

    with pytest.raises(ScaffoldExistsError):
        scaffold(tmp_path, _claude_code_podman())

    # No files leaked into the directory beyond what was already there.
    assert sorted(p.name for p in existing.iterdir()) == ["sentinel.txt"]


def test_scaffolded_driver_parses_as_python(tmp_path: Path) -> None:
    scaffold(tmp_path, _claude_code_podman())

    source = (tmp_path / ".pysolated" / "main.py").read_text(encoding="utf-8")
    # ast.parse raises SyntaxError on a malformed file — passes silently when ok.
    ast.parse(source)


def test_scaffolded_driver_compiles_into_a_real_module(tmp_path: Path) -> None:
    """A stronger check than `ast.parse`: import the scaffolded driver.

    Per ADR 0012, the driver *template* is not standalone-runnable, but the
    *scaffolded output* is expected to be a syntactically valid Python module
    that imports cleanly (its dependencies are already on the test path).
    Importing as `__main__` would run `asyncio.run(main())`; importing under
    a non-`__main__` name leaves the `if __name__ == "__main__"` branch
    inert, exercising only the module body.
    """
    import importlib.util

    scaffold(tmp_path, _claude_code_podman())

    driver_path = tmp_path / ".pysolated" / "main.py"
    spec = importlib.util.spec_from_file_location("_scaffolded_driver", driver_path)
    assert spec is not None and spec.loader is not None
    module = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(module)
    assert hasattr(module, "main")


def test_scaffolded_driver_has_no_unfilled_placeholders(tmp_path: Path) -> None:
    scaffold(tmp_path, _claude_code_podman())

    source = (tmp_path / ".pysolated" / "main.py").read_text(encoding="utf-8")
    assert "{{" not in source
    assert "}}" not in source


def test_scaffolded_driver_imports_chosen_agent_and_sandbox(tmp_path: Path) -> None:
    scaffold(tmp_path, _claude_code_podman())

    source = (tmp_path / ".pysolated" / "main.py").read_text(encoding="utf-8")
    assert "claude_code" in source
    assert "podman" in source


def test_scaffolded_driver_passes_model_to_agent_factory(tmp_path: Path) -> None:
    scaffold(tmp_path, _claude_code_podman(model="claude-haiku-4-5"))

    source = (tmp_path / ".pysolated" / "main.py").read_text(encoding="utf-8")
    assert "claude-haiku-4-5" in source


def test_scaffolded_driver_forwards_env_via_dotenv_values(tmp_path: Path) -> None:
    """Per ADR 0012, the driver loads `.pysolated/.env` and passes it via `env=`.

    Forwarding generically (rather than rewriting per-agent env-key lookups)
    keeps the driver agent-agnostic.
    """
    scaffold(tmp_path, _claude_code_podman())

    source = (tmp_path / ".pysolated" / "main.py").read_text(encoding="utf-8")
    assert "dotenv_values" in source
    assert "env=" in source


def test_scaffolded_containerfile_has_no_unfilled_placeholders(tmp_path: Path) -> None:
    scaffold(tmp_path, _claude_code_podman())

    text = (tmp_path / ".pysolated" / "Containerfile").read_text(encoding="utf-8")
    assert "{{" not in text
    assert "}}" not in text


def test_scaffolded_containerfile_uses_podman_keep_id_base(tmp_path: Path) -> None:
    """Per ADR 0011, the podman base has no UID build-arg (keep-id handles UIDs).

    The docker base — added in the 2×2 widening — does carry the
    AGENT_UID/AGENT_GID build-arg block, but the podman base must not.
    """
    scaffold(tmp_path, _claude_code_podman())

    text = (tmp_path / ".pysolated" / "Containerfile").read_text(encoding="utf-8")
    assert "AGENT_UID" not in text
    assert "AGENT_GID" not in text


def test_scaffolded_containerfile_installs_claude_code_after_user_directive(
    tmp_path: Path,
) -> None:
    """Claude Code's installer targets `$HOME/.local/bin`, so it must run after `USER`."""
    scaffold(tmp_path, _claude_code_podman())

    text = (tmp_path / ".pysolated" / "Containerfile").read_text(encoding="utf-8")
    user_pos = text.find("USER ")
    install_pos = text.find("claude.ai/install.sh")
    assert user_pos != -1, "missing USER directive"
    assert install_pos != -1, "missing claude-code install line"
    assert install_pos > user_pos


def test_gitignore_ignores_env_file(tmp_path: Path) -> None:
    scaffold(tmp_path, _claude_code_podman())

    text = (tmp_path / ".pysolated" / ".gitignore").read_text(encoding="utf-8")
    assert ".env" in text.splitlines()


def test_env_example_carries_claude_code_credential_block(tmp_path: Path) -> None:
    """The .env.example documents *which* keys to set for the chosen agent."""
    scaffold(tmp_path, _claude_code_podman())

    text = (tmp_path / ".pysolated" / ".env.example").read_text(encoding="utf-8")
    assert "CLAUDE_CODE_OAUTH_TOKEN" in text


def test_prompt_md_is_written_as_skeleton(tmp_path: Path) -> None:
    scaffold(tmp_path, _claude_code_podman())

    text = (tmp_path / ".pysolated" / "prompt.md").read_text(encoding="utf-8")
    assert text.strip(), "prompt.md should not be empty"


def test_scaffold_codex_podman_driver_imports_codex(tmp_path: Path) -> None:
    scaffold(tmp_path, _codex_podman())

    source = (tmp_path / ".pysolated" / "main.py").read_text(encoding="utf-8")
    assert "codex" in source
    assert "podman" in source


def test_scaffold_codex_podman_env_example_carries_codex_credentials(
    tmp_path: Path,
) -> None:
    scaffold(tmp_path, _codex_podman())

    text = (tmp_path / ".pysolated" / ".env.example").read_text(encoding="utf-8")
    assert "OPENAI_API_KEY" in text


def test_scaffold_codex_podman_installs_codex_before_user_directive(
    tmp_path: Path,
) -> None:
    """Per ADR 0011, codex installs as root via `npm i -g` *before* `USER`."""
    scaffold(tmp_path, _codex_podman())

    text = (tmp_path / ".pysolated" / "Containerfile").read_text(encoding="utf-8")
    user_pos = text.find("USER ")
    install_pos = text.find("npm")
    assert user_pos != -1, "missing USER directive"
    assert install_pos != -1, "missing codex npm-install line"
    assert install_pos < user_pos


def test_scaffolded_codex_driver_compiles_into_a_real_module(tmp_path: Path) -> None:
    import importlib.util

    scaffold(tmp_path, _codex_podman())

    driver_path = tmp_path / ".pysolated" / "main.py"
    spec = importlib.util.spec_from_file_location(
        "_scaffolded_codex_driver", driver_path
    )
    assert spec is not None and spec.loader is not None
    module = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(module)
    assert hasattr(module, "main")


def test_scaffold_docker_writes_dockerfile_not_containerfile(tmp_path: Path) -> None:
    """Per ADR 0011, the docker base ships as `Dockerfile`, not `Containerfile`."""
    scaffold(tmp_path, _claude_code_docker())

    config = tmp_path / ".pysolated"
    assert (config / "Dockerfile").is_file()
    assert not (config / "Containerfile").exists()


def test_scaffold_docker_base_carries_agent_uid_build_args(tmp_path: Path) -> None:
    """Per ADR 0005, the docker base declares AGENT_UID/AGENT_GID build-args."""
    scaffold(tmp_path, _claude_code_docker())

    text = (tmp_path / ".pysolated" / "Dockerfile").read_text(encoding="utf-8")
    assert "ARG AGENT_UID" in text
    assert "ARG AGENT_GID" in text


def test_scaffold_docker_base_uses_numeric_user_directive(tmp_path: Path) -> None:
    """Per ADR 0005, the docker base's `USER` must be numeric so the preflight can parse it."""
    scaffold(tmp_path, _claude_code_docker())

    text = (tmp_path / ".pysolated" / "Dockerfile").read_text(encoding="utf-8")
    # The numeric form references the build-args, not a username like `USER agent`.
    assert "USER ${AGENT_UID}" in text


def test_scaffold_docker_claude_code_installs_after_numeric_user(
    tmp_path: Path,
) -> None:
    scaffold(tmp_path, _claude_code_docker())

    text = (tmp_path / ".pysolated" / "Dockerfile").read_text(encoding="utf-8")
    user_pos = text.find("USER ${AGENT_UID}")
    install_pos = text.find("claude.ai/install.sh")
    assert user_pos != -1
    assert install_pos != -1
    assert install_pos > user_pos


def test_scaffold_codex_docker_installs_codex_before_numeric_user(
    tmp_path: Path,
) -> None:
    scaffold(tmp_path, _codex_docker())

    text = (tmp_path / ".pysolated" / "Dockerfile").read_text(encoding="utf-8")
    user_pos = text.find("USER ${AGENT_UID}")
    install_pos = text.find("npm")
    assert user_pos != -1
    assert install_pos != -1
    assert install_pos < user_pos


def test_scaffold_docker_driver_imports_docker_factory(tmp_path: Path) -> None:
    scaffold(tmp_path, _claude_code_docker())

    source = (tmp_path / ".pysolated" / "main.py").read_text(encoding="utf-8")
    assert "from pysolated import" in source
    assert "docker" in source


def test_scaffolded_docker_driver_compiles_into_a_real_module(tmp_path: Path) -> None:
    import importlib.util

    scaffold(tmp_path, _claude_code_docker())

    driver_path = tmp_path / ".pysolated" / "main.py"
    spec = importlib.util.spec_from_file_location(
        "_scaffolded_docker_driver", driver_path
    )
    assert spec is not None and spec.loader is not None
    module = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(module)
    assert hasattr(module, "main")


def test_scaffold_codex_docker_combines_codex_install_and_docker_uid_args(
    tmp_path: Path,
) -> None:
    """The full 2×2 corner: codex install snippet + docker UID-align block."""
    scaffold(tmp_path, _codex_docker())

    text = (tmp_path / ".pysolated" / "Dockerfile").read_text(encoding="utf-8")
    assert "ARG AGENT_UID" in text
    assert "npm" in text
    assert "{{" not in text
    assert "}}" not in text
