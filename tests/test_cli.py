"""CLI surface tests — top-level flags, `check` subcommand outputs,
and the `llm doctor` diagnostic command."""
from __future__ import annotations

import json
from pathlib import Path

import pytest
from click.testing import CliRunner

from speclint import __version__
from speclint.cli import main
from speclint.llm import TransportProbe


@pytest.fixture(autouse=True)
def clean_llm_env(monkeypatch):
    for var in ("ANTHROPIC_API_KEY", "OPENAI_API_KEY", "OPENAI_BASE_URL"):
        monkeypatch.delenv(var, raising=False)


def _seed_clean_repo(tmp_path: Path) -> Path:
    (tmp_path / "specs" / "demo").mkdir(parents=True)
    (tmp_path / "specs" / "demo" / "spec.yml").write_text("id: demo\nstatus: accepted\n")
    (tmp_path / "specs" / "demo" / "README.md").write_text("# demo\n")
    return tmp_path


def _seed_failing_repo(tmp_path: Path) -> Path:
    (tmp_path / "specs" / "demo").mkdir(parents=True)
    (tmp_path / "specs" / "demo" / "spec.yml").write_text("id: demo\nstatus: accepted\n")
    (tmp_path / "specs" / "demo" / "README.md").write_text("# demo\n\nTBD: do it.\n")
    return tmp_path


# ---------- top-level ----------

def test_version_flag_prints_version():
    runner = CliRunner()
    result = runner.invoke(main, ["--version"])
    assert result.exit_code == 0
    assert __version__ in result.output


def test_no_subcommand_shows_help():
    runner = CliRunner()
    result = runner.invoke(main, [])
    assert result.exit_code == 0
    assert "Usage:" in result.output


# ---------- check ----------

def test_check_human_format_clean_repo(tmp_path):
    _seed_clean_repo(tmp_path)
    runner = CliRunner()
    result = runner.invoke(main, ["check", str(tmp_path)])
    assert result.exit_code == 0
    assert "summary:" in result.output
    assert "0 error" in result.output


def test_check_json_format_emits_parseable_payload(tmp_path):
    _seed_clean_repo(tmp_path)
    runner = CliRunner()
    result = runner.invoke(main, ["check", str(tmp_path), "--format", "json"])
    assert result.exit_code == 0
    payload = json.loads(result.output)
    assert payload["schema"] == "speclint/v1"
    assert payload["summary"]["specs_checked"] == ["demo"]


def test_check_markdown_format_includes_marker(tmp_path):
    _seed_clean_repo(tmp_path)
    runner = CliRunner()
    result = runner.invoke(main, ["check", str(tmp_path), "--format", "markdown"])
    assert result.exit_code == 0
    assert "<!-- speclint:report -->" in result.output


def test_check_github_format_silent_when_no_findings(tmp_path):
    _seed_clean_repo(tmp_path)
    runner = CliRunner()
    result = runner.invoke(main, ["check", str(tmp_path), "--format", "github"])
    assert result.exit_code == 0
    # Clean repo → no workflow commands emitted
    assert "::warning" not in result.output
    assert "::error" not in result.output


def test_check_fail_on_warn_returns_1_with_warnings(tmp_path):
    _seed_failing_repo(tmp_path)
    runner = CliRunner()
    result = runner.invoke(main, ["check", str(tmp_path), "--fail-on", "warn"])
    assert result.exit_code == 1


def test_check_fail_on_never_returns_0_with_warnings(tmp_path):
    _seed_failing_repo(tmp_path)
    runner = CliRunner()
    result = runner.invoke(main, ["check", str(tmp_path), "--fail-on", "never"])
    assert result.exit_code == 0


def test_check_handles_speclint_error_with_exit_2(tmp_path):
    """SpecLintError from config parsing surfaces as exit 2, not a traceback."""
    (tmp_path / ".speclint.yml").write_text("fail_on: maybe\n")
    runner = CliRunner()
    result = runner.invoke(main, ["check", str(tmp_path)])
    assert result.exit_code == 2
    assert "speclint:" in result.output
    assert "Traceback" not in result.output


def test_check_invalid_format_choice_fails(tmp_path):
    _seed_clean_repo(tmp_path)
    runner = CliRunner()
    result = runner.invoke(main, ["check", str(tmp_path), "--format", "xml"])
    assert result.exit_code != 0
    assert "Invalid value" in result.output or "invalid choice" in result.output.lower()


def test_check_invalid_repo_path_fails():
    runner = CliRunner()
    result = runner.invoke(main, ["check", "/does/not/exist"])
    assert result.exit_code != 0


# ---------- llm doctor ----------

def test_llm_doctor_reports_no_transport_when_none_available(monkeypatch):
    runner = CliRunner()
    monkeypatch.setattr(
        "speclint.cli.probe_transports",
        lambda: [
            TransportProbe(name="api", available=False, reason="no ANTHROPIC_API_KEY"),
            TransportProbe(name="openai", available=False, reason="no OPENAI_API_KEY"),
            TransportProbe(name="cli", available=False, reason="claude not on PATH"),
            TransportProbe(name="codex", available=False, reason="codex not on PATH"),
        ],
    )
    monkeypatch.setattr("speclint.cli.select_transport", lambda _: None)

    result = runner.invoke(main, ["llm", "doctor"])
    assert result.exit_code == 0
    assert "transport probe" in result.output
    assert "✗" in result.output
    assert "chosen: none" in result.output
    assert "ANTHROPIC_API_KEY" in result.output  # the hint


def test_llm_doctor_reports_chosen_transport(monkeypatch):
    runner = CliRunner()
    monkeypatch.setattr(
        "speclint.cli.probe_transports",
        lambda: [
            TransportProbe(name="api", available=True, reason="ANTHROPIC_API_KEY set"),
            TransportProbe(name="openai", available=False, reason="no OPENAI_API_KEY"),
            TransportProbe(name="cli", available=False, reason="claude not on PATH"),
            TransportProbe(name="codex", available=False, reason="codex not on PATH"),
        ],
    )
    monkeypatch.setattr("speclint.cli.select_transport", lambda _: "api")

    result = runner.invoke(main, ["llm", "doctor"])
    assert result.exit_code == 0
    assert "chosen: api" in result.output
    assert "default model" in result.output


def test_llm_doctor_handles_speclint_error(monkeypatch):
    from speclint.rules.registry import SpecLintError

    runner = CliRunner()
    monkeypatch.setattr("speclint.cli.probe_transports", lambda: [])

    def boom(_):
        raise SpecLintError("oops")

    monkeypatch.setattr("speclint.cli.select_transport", boom)
    result = runner.invoke(main, ["llm", "doctor"])
    assert result.exit_code == 0  # doctor itself never exits non-zero
    assert "error: oops" in result.output
