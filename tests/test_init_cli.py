"""CLI tests for `pysolated init` — the wizard's all-flags (headless) path.

Wizard prompts are deferred to #48 (the tri-state slice). The CLI is
flag/default-driven and exercises `scaffold()` end-to-end through the Typer
entry point across the agent × sandbox 2×2.
"""

from __future__ import annotations

from pathlib import Path

from typer.testing import CliRunner

from pysolated import cli as cli_module


def test_init_scaffolds_default_combo_into_cwd(tmp_path: Path) -> None:
    runner = CliRunner()
    result = runner.invoke(cli_module.app, ["init", "--cwd", str(tmp_path)])
    assert result.exit_code == 0, result.output

    config = tmp_path / ".pysolated"
    assert (config / "main.py").is_file()
    assert (config / "prompt.md").is_file()
    assert (config / "Containerfile").is_file()
    assert (config / ".gitignore").is_file()
    assert (config / ".env.example").is_file()


def test_init_prints_build_image_next_step(tmp_path: Path) -> None:
    runner = CliRunner()
    result = runner.invoke(cli_module.app, ["init", "--cwd", str(tmp_path)])
    assert result.exit_code == 0, result.output
    assert "pysolated podman build-image" in result.output


def test_init_fails_with_clear_message_when_config_already_exists(
    tmp_path: Path,
) -> None:
    (tmp_path / ".pysolated").mkdir()

    runner = CliRunner()
    result = runner.invoke(cli_module.app, ["init", "--cwd", str(tmp_path)])
    assert result.exit_code != 0
    assert ".pysolated" in result.output


def test_init_rejects_unknown_agent_with_exit_2(tmp_path: Path) -> None:
    runner = CliRunner()
    result = runner.invoke(
        cli_module.app,
        ["init", "--cwd", str(tmp_path), "--agent", "bogus"],
    )
    assert result.exit_code == 2, result.output
    assert "bogus" in result.output


def test_init_rejects_unknown_sandbox_with_exit_2(tmp_path: Path) -> None:
    runner = CliRunner()
    result = runner.invoke(
        cli_module.app,
        ["init", "--cwd", str(tmp_path), "--sandbox", "kubernetes"],
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
        ["init", "--cwd", str(tmp_path), "--agent", "codex"],
    )
    assert result.exit_code == 0, result.output

    env_example = (tmp_path / ".pysolated" / ".env.example").read_text(encoding="utf-8")
    assert "OPENAI_API_KEY" in env_example


def test_init_codex_defaults_to_codex_default_model(tmp_path: Path) -> None:
    """`--model` omitted should fall through to the codex agent's default."""
    runner = CliRunner()
    result = runner.invoke(
        cli_module.app,
        ["init", "--cwd", str(tmp_path), "--agent", "codex"],
    )
    assert result.exit_code == 0, result.output

    driver = (tmp_path / ".pysolated" / "main.py").read_text(encoding="utf-8")
    assert "gpt-5-codex" in driver


def test_init_accepts_docker_sandbox_and_writes_dockerfile(tmp_path: Path) -> None:
    runner = CliRunner()
    result = runner.invoke(
        cli_module.app,
        ["init", "--cwd", str(tmp_path), "--sandbox", "docker"],
    )
    assert result.exit_code == 0, result.output

    config = tmp_path / ".pysolated"
    assert (config / "Dockerfile").is_file()
    assert not (config / "Containerfile").exists()


def test_init_docker_next_step_points_to_docker_build_image(tmp_path: Path) -> None:
    runner = CliRunner()
    result = runner.invoke(
        cli_module.app,
        ["init", "--cwd", str(tmp_path), "--sandbox", "docker"],
    )
    assert result.exit_code == 0, result.output
    assert "pysolated docker build-image" in result.output
