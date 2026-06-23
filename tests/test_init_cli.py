"""CLI tests for `pysolated init` — the wizard's tri-state (#48) flag/TTY paths.

The wizard resolves each of `--agent` and `--sandbox` per-choice:
  - flag present → use it (these are the all-flags happy paths below);
  - flag absent + TTY → prompt with rich (HITL-tested in #49);
  - flag absent + no TTY → fail fast naming the missing flag (the headless
    failure paths below).

Tests target the all-flags path and the headless failure modes; the
interactive rich prompts are a thin shell and not the unit-test surface
(ADR 0010).
"""

from __future__ import annotations

from pathlib import Path

import pytest
from rich.prompt import Prompt
from typer.testing import CliRunner

from pysolated import cli as cli_module


def test_init_scaffolds_explicit_combo_into_cwd(tmp_path: Path) -> None:
    runner = CliRunner()
    result = runner.invoke(
        cli_module.app,
        [
            "init",
            "--cwd",
            str(tmp_path),
            "--agent",
            "claude-code",
            "--sandbox",
            "podman",
        ],
    )
    assert result.exit_code == 0, result.output

    config = tmp_path / ".pysolated"
    assert (config / "main.py").is_file()
    assert (config / "prompt.md").is_file()
    assert (config / "Containerfile").is_file()
    assert (config / ".gitignore").is_file()
    assert (config / ".env.example").is_file()


def test_init_prints_build_image_next_step(tmp_path: Path) -> None:
    runner = CliRunner()
    result = runner.invoke(
        cli_module.app,
        [
            "init",
            "--cwd",
            str(tmp_path),
            "--agent",
            "claude-code",
            "--sandbox",
            "podman",
        ],
    )
    assert result.exit_code == 0, result.output
    assert "pysolated podman build-image" in result.output


def test_init_headless_missing_agent_flag_exits_2_naming_the_flag(
    tmp_path: Path,
) -> None:
    """no TTY + `--agent` absent → fail fast naming `--agent` (ADR 0010)."""
    runner = CliRunner()
    result = runner.invoke(
        cli_module.app,
        ["init", "--cwd", str(tmp_path), "--sandbox", "podman"],
    )
    assert result.exit_code == 2, result.output
    assert "--agent" in result.output
    assert not (tmp_path / ".pysolated").exists()


def test_init_headless_missing_sandbox_flag_exits_2_naming_the_flag(
    tmp_path: Path,
) -> None:
    """no TTY + `--sandbox` absent → fail fast naming `--sandbox`."""
    runner = CliRunner()
    result = runner.invoke(
        cli_module.app,
        ["init", "--cwd", str(tmp_path), "--agent", "claude-code"],
    )
    assert result.exit_code == 2, result.output
    assert "--sandbox" in result.output
    assert not (tmp_path / ".pysolated").exists()


def test_init_headless_both_flags_missing_fails_on_first_missing_choice(
    tmp_path: Path,
) -> None:
    """Both flags absent in a non-TTY → fail fast naming the first gap."""
    runner = CliRunner()
    result = runner.invoke(cli_module.app, ["init", "--cwd", str(tmp_path)])
    assert result.exit_code == 2, result.output
    assert "--agent" in result.output
    assert not (tmp_path / ".pysolated").exists()


def test_init_tty_missing_flags_prompts_and_scaffolds(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    """TTY + both flags absent → rich prompts the user, then scaffold() runs.

    Simulates a TTY by overriding `_is_interactive()` and intercepting
    rich's `Prompt.ask` to return the choices a user would have typed.
    """
    monkeypatch.setattr(cli_module, "_is_interactive", lambda: True)

    answers = iter(["codex", "docker"])
    monkeypatch.setattr(
        Prompt,
        "ask",
        classmethod(lambda cls, *a, **kw: next(answers)),
    )

    runner = CliRunner()
    result = runner.invoke(cli_module.app, ["init", "--cwd", str(tmp_path)])
    assert result.exit_code == 0, result.output

    config = tmp_path / ".pysolated"
    assert (config / "Dockerfile").is_file()
    env_example = (config / ".env.example").read_text(encoding="utf-8")
    assert "OPENAI_API_KEY" in env_example


def test_init_tty_skips_prompt_for_flag_that_is_provided(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    """A provided flag suppresses its prompt even in a TTY (only the gap is asked)."""
    monkeypatch.setattr(cli_module, "_is_interactive", lambda: True)

    asked_labels: list[str] = []

    def fake_ask(cls: type, prompt: str, /, *a: object, **kw: object) -> str:
        asked_labels.append(prompt)
        return "docker"

    monkeypatch.setattr(Prompt, "ask", classmethod(fake_ask))

    runner = CliRunner()
    result = runner.invoke(
        cli_module.app,
        ["init", "--cwd", str(tmp_path), "--agent", "claude-code"],
    )
    assert result.exit_code == 0, result.output
    # Only the missing choice (sandbox) was prompted; agent was not.
    assert asked_labels == ["Select sandbox"]
    assert (tmp_path / ".pysolated" / "Dockerfile").is_file()


def test_init_fails_with_clear_message_when_config_already_exists(
    tmp_path: Path,
) -> None:
    (tmp_path / ".pysolated").mkdir()

    runner = CliRunner()
    result = runner.invoke(
        cli_module.app,
        [
            "init",
            "--cwd",
            str(tmp_path),
            "--agent",
            "claude-code",
            "--sandbox",
            "podman",
        ],
    )
    assert result.exit_code != 0
    assert ".pysolated" in result.output


def test_init_rejects_unknown_agent_with_exit_2(tmp_path: Path) -> None:
    runner = CliRunner()
    result = runner.invoke(
        cli_module.app,
        ["init", "--cwd", str(tmp_path), "--agent", "bogus", "--sandbox", "podman"],
    )
    assert result.exit_code == 2, result.output
    assert "bogus" in result.output


def test_init_rejects_unknown_sandbox_with_exit_2(tmp_path: Path) -> None:
    runner = CliRunner()
    result = runner.invoke(
        cli_module.app,
        [
            "init",
            "--cwd",
            str(tmp_path),
            "--agent",
            "claude-code",
            "--sandbox",
            "kubernetes",
        ],
    )
    assert result.exit_code == 2, result.output
    assert "kubernetes" in result.output


def test_init_model_flag_flows_into_scaffolded_driver(tmp_path: Path) -> None:
    runner = CliRunner()
    result = runner.invoke(
        cli_module.app,
        [
            "init",
            "--cwd",
            str(tmp_path),
            "--agent",
            "claude-code",
            "--sandbox",
            "podman",
            "--model",
            "claude-sonnet-4-6",
        ],
    )
    assert result.exit_code == 0, result.output

    driver = (tmp_path / ".pysolated" / "main.py").read_text(encoding="utf-8")
    assert "claude-sonnet-4-6" in driver


def test_init_accepts_codex_agent_and_writes_codex_credentials(
    tmp_path: Path,
) -> None:
    runner = CliRunner()
    result = runner.invoke(
        cli_module.app,
        ["init", "--cwd", str(tmp_path), "--agent", "codex", "--sandbox", "podman"],
    )
    assert result.exit_code == 0, result.output

    env_example = (tmp_path / ".pysolated" / ".env.example").read_text(encoding="utf-8")
    assert "OPENAI_API_KEY" in env_example


def test_init_codex_defaults_to_codex_default_model(tmp_path: Path) -> None:
    """`--model` omitted should fall through to the codex agent's default."""
    runner = CliRunner()
    result = runner.invoke(
        cli_module.app,
        ["init", "--cwd", str(tmp_path), "--agent", "codex", "--sandbox", "podman"],
    )
    assert result.exit_code == 0, result.output

    driver = (tmp_path / ".pysolated" / "main.py").read_text(encoding="utf-8")
    assert "gpt-5-codex" in driver


def test_init_accepts_docker_sandbox_and_writes_dockerfile(tmp_path: Path) -> None:
    runner = CliRunner()
    result = runner.invoke(
        cli_module.app,
        [
            "init",
            "--cwd",
            str(tmp_path),
            "--agent",
            "claude-code",
            "--sandbox",
            "docker",
        ],
    )
    assert result.exit_code == 0, result.output

    config = tmp_path / ".pysolated"
    assert (config / "Dockerfile").is_file()
    assert not (config / "Containerfile").exists()


def test_init_docker_next_step_points_to_docker_build_image(tmp_path: Path) -> None:
    runner = CliRunner()
    result = runner.invoke(
        cli_module.app,
        [
            "init",
            "--cwd",
            str(tmp_path),
            "--agent",
            "claude-code",
            "--sandbox",
            "docker",
        ],
    )
    assert result.exit_code == 0, result.output
    assert "pysolated docker build-image" in result.output
