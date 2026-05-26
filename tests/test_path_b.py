"""Path B integration: diff resolution, runner threading, end-to-end coupling.

Fixture-level rule behavior lives in `test_fixtures.py` via the per-rule
fixtures. This file exercises the wiring above the rule — how diff context
reaches SpecIR.changed_paths and how the CLI surfaces it.
"""
from __future__ import annotations

import subprocess
from pathlib import Path

import pytest
from click.testing import CliRunner

from speclint.cli import main
from speclint.config import Config, MetadataConfig
from speclint.diff import from_file, from_git
from speclint.rules.registry import SpecLintError
from speclint.runner import run


def _make_repo(tmp_path: Path, *, sidecar_yaml: str, repo_files: dict[str, str]) -> Path:
    """Lay down a single spec + extra repo files. Returns repo root.

    The sidecar lands as `meta.yml` inside the spec folder; callers must
    configure ``Config.metadata.sidecar = "meta.yml"`` for rules to see
    its contents.
    """
    spec = tmp_path / "specs" / "demo"
    spec.mkdir(parents=True)
    (spec / "meta.yml").write_text(sidecar_yaml, encoding="utf-8")
    (spec / "README.md").write_text("# demo\n", encoding="utf-8")
    for rel, content in repo_files.items():
        full = tmp_path / rel
        full.parent.mkdir(parents=True, exist_ok=True)
        full.write_text(content, encoding="utf-8")
    return tmp_path


def _cfg_with_sidecar() -> Config:
    cfg = Config()
    cfg.metadata = MetadataConfig(sidecar="meta.yml")
    return cfg


# ---------- diff resolution ----------

def test_from_file_reads_changed_paths(tmp_path):
    f = tmp_path / "changed.txt"
    f.write_text("src/a.py\n  src/b.py  \n\nsrc/c.py\n", encoding="utf-8")
    assert from_file(f) == ("src/a.py", "src/b.py", "src/c.py")


def test_from_file_missing_raises(tmp_path):
    with pytest.raises(SpecLintError, match="file not found"):
        from_file(tmp_path / "nope.txt")


def test_from_git_against_real_repo(tmp_path):
    """Init a real git repo, stage two files in a branch, verify the diff
    is parsed correctly. Skips cleanly if git isn't installed."""
    try:
        subprocess.run(["git", "--version"], check=True, capture_output=True)
    except (FileNotFoundError, subprocess.CalledProcessError):
        pytest.skip("git not available")

    def git(*args: str) -> None:
        subprocess.run(["git", "-C", str(tmp_path), *args], check=True, capture_output=True)

    git("init", "-q", "-b", "main")
    git("config", "user.email", "t@t")
    git("config", "user.name", "t")
    (tmp_path / "a.py").write_text("a\n")
    git("add", "a.py")
    git("commit", "-q", "-m", "initial")
    git("checkout", "-q", "-b", "feature")
    (tmp_path / "b.py").write_text("b\n")
    (tmp_path / "a.py").write_text("a-changed\n")
    git("add", "a.py", "b.py")
    git("commit", "-q", "-m", "feature")

    changed = from_git(tmp_path, "main")
    assert set(changed) == {"a.py", "b.py"}


def test_from_git_invalid_base_raises(tmp_path):
    try:
        subprocess.run(["git", "--version"], check=True, capture_output=True)
    except (FileNotFoundError, subprocess.CalledProcessError):
        pytest.skip("git not available")
    subprocess.run(["git", "-C", str(tmp_path), "init", "-q"], check=True)
    with pytest.raises(SpecLintError, match="git diff failed"):
        from_git(tmp_path, "no-such-ref")


# ---------- runner threading ----------

def test_runner_threads_changed_paths_into_coupling_rule(tmp_path):
    root = _make_repo(
        tmp_path,
        sidecar_yaml="status: accepted\nreferences:\n  - 'src/demo/**'\n",
        repo_files={"src/demo/a.py": "x = 1\n"},
    )
    result = run(root, _cfg_with_sidecar(), changed_paths=("src/demo/a.py",))

    coupling = [f for f in result.findings if f.rule_id == "refs-coupling"]
    assert len(coupling) == 1
    assert "src/demo/a.py" in coupling[0].message
    assert coupling[0].spec == "demo"


def test_runner_without_changed_paths_skips_coupling(tmp_path):
    root = _make_repo(
        tmp_path,
        sidecar_yaml="status: accepted\nreferences:\n  - 'src/demo/**'\n",
        repo_files={"src/demo/a.py": "x = 1\n"},
    )
    result = run(root, _cfg_with_sidecar())
    assert not [f for f in result.findings if f.rule_id == "refs-coupling"]


def test_runner_spec_co_change_silences_coupling(tmp_path):
    """When the spec folder itself was modified in the same diff, no fire."""
    root = _make_repo(
        tmp_path,
        sidecar_yaml="status: accepted\nreferences:\n  - 'src/demo/**'\n",
        repo_files={"src/demo/a.py": "x = 1\n"},
    )
    changed = ("src/demo/a.py", "specs/demo/README.md")
    result = run(root, _cfg_with_sidecar(), changed_paths=changed)
    assert not [f for f in result.findings if f.rule_id == "refs-coupling"]


# ---------- CLI ----------

def test_cli_rejects_both_base_and_changed_files_from(tmp_path):
    runner = CliRunner()
    result = runner.invoke(main, ["check", str(tmp_path), "--base", "main",
                                  "--changed-files-from", "x.txt"])
    assert result.exit_code == 2
    assert "mutually exclusive" in result.output


def test_cli_changed_files_from_triggers_coupling(tmp_path):
    root = _make_repo(
        tmp_path,
        sidecar_yaml="status: accepted\nreferences:\n  - 'src/demo/**'\n",
        repo_files={"src/demo/a.py": "x = 1\n"},
    )
    # CLI needs a real `.speclint.yml` so the sidecar loader is on.
    (root / ".speclint.yml").write_text(
        "metadata:\n  sidecar: meta.yml\n", encoding="utf-8"
    )
    changed_file = root / "changed.txt"
    changed_file.write_text("src/demo/a.py\n")

    runner = CliRunner()
    result = runner.invoke(main, [
        "check", str(root),
        "--changed-files-from", str(changed_file),
        "--format", "json",
        "--fail-on", "never",
    ])
    assert result.exit_code == 0, result.output
    assert "refs-coupling" in result.output
    assert "src/demo/a.py" in result.output
