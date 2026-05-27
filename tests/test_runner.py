from pathlib import Path

from speclint.config import Config
from speclint.runner import exit_code_for, run

REPO = Path(__file__).resolve().parent.parent


def test_run_finds_findings_in_example_spec():
    cfg = Config()
    result = run(REPO, cfg)
    assert "example-spec" in result.specs_checked
    ids = {f.rule_id for f in result.findings}
    assert "no-tbd" in ids
    assert "no-weasel-words" in ids


def test_severity_override_via_config():
    cfg = Config()
    from speclint.config import RuleConfig

    cfg.rules["no-tbd"] = RuleConfig(severity="error")
    result = run(REPO, cfg)
    tbd_findings = [f for f in result.findings if f.rule_id == "no-tbd"]
    assert tbd_findings
    assert all(f.severity == "error" for f in tbd_findings)


def test_off_disables_rule():
    cfg = Config()
    from speclint.config import RuleConfig

    cfg.rules["no-tbd"] = RuleConfig(severity="off")
    result = run(REPO, cfg)
    assert not any(f.rule_id == "no-tbd" for f in result.findings)


def test_exit_code_fail_on_warn():
    cfg = Config()
    result = run(REPO, cfg)
    # example-spec has warn-level findings (no-tbd default = warn)
    assert exit_code_for(result, "warn") == 1
    # Default is fail_on=error; warns alone don't fail
    assert exit_code_for(result, "error") == 0
    assert exit_code_for(result, "never") == 0


def test_run_skips_llm_rule_when_no_transport_available(monkeypatch):
    """The default package ships `no-weasel-words-llm`. Without a transport
    on the test runner, it must be skipped (not crash) and reported."""
    monkeypatch.setattr("speclint.runner.select_transport", lambda _: None)
    cfg = Config()
    result = run(REPO, cfg)
    assert set(result.rules_skipped_llm) == {"no-weasel-words-llm", "spec-impl-drift"}
    assert result.transport_chosen is None
