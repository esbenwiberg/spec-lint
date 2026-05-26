"""Runner edge cases — LLM transport branches and per-rule crash isolation.

These tests inject synthetic rules (LLM-tier and crash-prone) into the
registry via monkeypatching, so we exercise the runner branches without
needing a real LLM provider or a buggy production rule."""
from __future__ import annotations

from pathlib import Path

import pytest

from speclint.config import Config, LLMConfig
from speclint.llm import TransportNotAvailable
from speclint.rules.registry import RuleRegistry
from speclint.rules.types import Finding, Rule
from speclint.runner import run


def _llm_rule(rule_id: str = "fake-llm-rule") -> Rule:
    def check(ir, config):
        return [Finding(rule_id=rule_id, severity="warn", file="x", line=1,
                        message="should never run when no transport", spec=None)]

    return Rule(
        id=rule_id, version="1.0.0", tier="llm",
        default_severity="warn", rationale="test only",
        check=check, package="default",
    )


def _crashing_rule(rule_id: str = "crashy") -> Rule:
    def check(ir, config):
        raise RuntimeError("kaboom from inside the rule")

    return Rule(
        id=rule_id, version="1.0.0", tier="static",
        default_severity="warn", rationale="test only",
        check=check, package="default",
    )


def _seed_repo(tmp_path: Path) -> Path:
    (tmp_path / "specs" / "demo").mkdir(parents=True)
    (tmp_path / "specs" / "demo" / "spec.yml").write_text("id: demo\nstatus: accepted\n")
    (tmp_path / "specs" / "demo" / "README.md").write_text("# demo\n")
    return tmp_path


def _patch_registry(monkeypatch, *rules: Rule) -> None:
    reg = RuleRegistry(
        rules={r.id: r for r in rules},
        overrides=[],
        package_order=["default"],
    )
    monkeypatch.setattr("speclint.runner.load_rule_packages", lambda _: reg)


# ---------- LLM transport branches ----------

def test_llm_rules_dropped_when_no_transport_available(tmp_path, monkeypatch):
    _seed_repo(tmp_path)
    _patch_registry(monkeypatch, _llm_rule("llm-a"), _llm_rule("llm-b"))
    monkeypatch.setattr("speclint.runner.select_transport", lambda _: None)

    result = run(tmp_path, Config())
    assert set(result.rules_skipped_llm) == {"llm-a", "llm-b"}
    assert result.transport_chosen is None
    # No findings — the LLM rules were dropped before execution
    assert all(f.rule_id not in {"llm-a", "llm-b"} for f in result.findings)


def test_llm_rules_dropped_when_llm_disabled_in_config(tmp_path, monkeypatch):
    _seed_repo(tmp_path)
    _patch_registry(monkeypatch, _llm_rule("llm-a"))

    cfg = Config()
    cfg.llm = LLMConfig(enabled=False)
    result = run(tmp_path, cfg)
    assert result.rules_skipped_llm == ["llm-a"]
    # transport_chosen stays None because we never resolved
    assert result.transport_chosen is None


def test_llm_rules_run_when_transport_available(tmp_path, monkeypatch):
    _seed_repo(tmp_path)
    _patch_registry(monkeypatch, _llm_rule("llm-a"))
    monkeypatch.setattr("speclint.runner.select_transport", lambda _: "cli")

    result = run(tmp_path, Config())
    assert result.rules_skipped_llm == []
    assert result.transport_chosen == "cli"
    # The synthetic rule emits one finding per spec
    assert any(f.rule_id == "llm-a" for f in result.findings)


def test_explicit_transport_failure_degrades_gracefully(tmp_path, monkeypatch):
    """When `transport: api` is set but the key is missing, the runner logs
    the failure and drops LLM rules — it must not crash the whole run."""
    _seed_repo(tmp_path)
    _patch_registry(monkeypatch, _llm_rule("llm-a"))

    def boom(_):
        raise TransportNotAvailable("no ANTHROPIC_API_KEY")

    monkeypatch.setattr("speclint.runner.select_transport", boom)

    cfg = Config()
    cfg.llm = LLMConfig(enabled=True, transport="api")
    result = run(tmp_path, cfg)
    assert result.rules_skipped_llm == ["llm-a"]
    assert result.transport_chosen is None


def _static_rule(rule_id: str = "static-noop") -> Rule:
    return Rule(
        id=rule_id, version="1.0.0", tier="static",
        default_severity="warn", rationale="test only",
        check=lambda ir, c: [], package="default",
    )


def test_no_llm_rules_means_transport_never_resolved(tmp_path, monkeypatch):
    """When the registry has zero LLM-tier rules, the runner must not even
    attempt transport resolution — saves a subprocess + env probe."""
    _seed_repo(tmp_path)
    # Patch to a static-only registry so the default package's LLM rule
    # doesn't trigger transport resolution.
    _patch_registry(monkeypatch, _static_rule())

    calls: list[str] = []

    def tracker(pref):
        calls.append(pref)
        return "api"

    monkeypatch.setattr("speclint.runner.select_transport", tracker)
    result = run(tmp_path, Config())
    assert calls == []
    assert result.transport_chosen is None


# ---------- per-rule crash isolation ----------

def test_rule_crash_produces_error_finding_not_traceback(tmp_path, monkeypatch):
    _seed_repo(tmp_path)
    _patch_registry(monkeypatch, _crashing_rule("crashy"))

    result = run(tmp_path, Config())
    crashes = [f for f in result.findings if f.rule_id == "crashy"]
    assert len(crashes) == 1
    assert crashes[0].severity == "error"
    assert "kaboom" in crashes[0].message
    assert crashes[0].spec == "demo"


def test_one_rule_crash_does_not_block_other_rules(tmp_path, monkeypatch):
    """A crashy rule must not silence neighboring rules in the same run."""
    _seed_repo(tmp_path)
    ok_rule = Rule(
        id="ok", version="1.0.0", tier="static",
        default_severity="warn", rationale="test",
        check=lambda ir, cfg: [Finding(rule_id="ok", severity="warn",
                                       file="f", line=1, message="hi", spec=None)],
        package="default",
    )
    _patch_registry(monkeypatch, _crashing_rule("crashy"), ok_rule)

    result = run(tmp_path, Config())
    ids = {f.rule_id for f in result.findings}
    assert "crashy" in ids and "ok" in ids


# ---------- result aggregation ----------

def test_run_result_records_package_order_and_overrides(tmp_path):
    _seed_repo(tmp_path)
    result = run(tmp_path, Config())
    assert result.package_order == ["default"]
    # No overrides when only one package
    assert result.override_log == []


def test_max_severity_rank_empty_result_is_negative_one():
    from speclint.runner import RunResult

    r = RunResult()
    assert r.max_severity_rank == -1


def test_max_severity_rank_ignores_off_severity():
    from speclint.runner import RunResult

    r = RunResult(findings=[
        Finding(rule_id="x", severity="off", file="f", line=1, message="m", spec="s"),
        Finding(rule_id="y", severity="info", file="f", line=1, message="m", spec="s"),
    ])
    # info ranks 0, off is filtered out
    assert r.max_severity_rank == 0
