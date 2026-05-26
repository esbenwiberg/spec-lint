"""`.speclint.yml` loading — full coverage of `load_config` branches."""
from __future__ import annotations

import textwrap

import pytest

from speclint.config import LLMConfig, RuleConfig, load_config
from speclint.rules.registry import SpecLintError


def _write(tmp_path, content: str):
    (tmp_path / ".speclint.yml").write_text(textwrap.dedent(content), encoding="utf-8")


def test_no_config_returns_defaults(tmp_path):
    cfg = load_config(tmp_path)
    assert cfg.packages == ["default"]
    assert cfg.specs == ["specs/*/"]
    assert cfg.include == ["**/*.md"]
    assert cfg.ignore == []
    assert cfg.fail_on == "error"
    assert cfg.rules == {}
    assert isinstance(cfg.llm, LLMConfig)


def test_empty_yaml_returns_defaults(tmp_path):
    _write(tmp_path, "")
    cfg = load_config(tmp_path)
    assert cfg.packages == ["default"]


def test_full_top_level_fields(tmp_path):
    _write(tmp_path, """
    packages: [default, my-team]
    specs: ["docs/specs/*/"]
    include: ["**/*.md", "**/*.mdx"]
    ignore: ["**/draft/*"]
    fail_on: warn
    """)
    cfg = load_config(tmp_path)
    assert cfg.packages == ["default", "my-team"]
    assert cfg.specs == ["docs/specs/*/"]
    assert cfg.include == ["**/*.md", "**/*.mdx"]
    assert cfg.ignore == ["**/draft/*"]
    assert cfg.fail_on == "warn"


def test_rule_string_form_sets_severity(tmp_path):
    _write(tmp_path, """
    rules:
      no-tbd: error
    """)
    cfg = load_config(tmp_path)
    rc = cfg.rules["no-tbd"]
    assert isinstance(rc, RuleConfig)
    assert rc.severity == "error"
    assert rc.options == {}


def test_rule_mapping_form_with_options(tmp_path):
    _write(tmp_path, """
    rules:
      no-tbd:
        severity: error
        patterns: ["TBD", "XXX"]
    """)
    cfg = load_config(tmp_path)
    rc = cfg.rules["no-tbd"]
    assert rc.severity == "error"
    assert rc.options == {"patterns": ["TBD", "XXX"]}


def test_rule_options_method_merges_severity(tmp_path):
    _write(tmp_path, """
    rules:
      no-tbd:
        severity: error
        patterns: ["TBD"]
    """)
    cfg = load_config(tmp_path)
    opts = cfg.rule_options("no-tbd")
    assert opts == {"severity": "error", "patterns": ["TBD"]}


def test_rule_options_method_unknown_rule_returns_empty(tmp_path):
    cfg = load_config(tmp_path)
    assert cfg.rule_options("does-not-exist") == {}


def test_rule_options_method_omits_severity_when_unset(tmp_path):
    _write(tmp_path, """
    rules:
      no-tbd:
        patterns: ["TBD"]
    """)
    cfg = load_config(tmp_path)
    opts = cfg.rule_options("no-tbd")
    assert "severity" not in opts
    assert opts["patterns"] == ["TBD"]


def test_llm_block_overrides_defaults(tmp_path):
    _write(tmp_path, """
    llm:
      enabled: false
      transport: api
      model: claude-opus-4-7
      cache: .cache/llm/
    """)
    cfg = load_config(tmp_path)
    assert cfg.llm.enabled is False
    assert cfg.llm.transport == "api"
    assert cfg.llm.model == "claude-opus-4-7"
    assert cfg.llm.cache == ".cache/llm/"


# ---------- error paths ----------

def test_invalid_yaml_raises(tmp_path):
    (tmp_path / ".speclint.yml").write_text("packages: [\n", encoding="utf-8")
    with pytest.raises(SpecLintError, match="invalid YAML"):
        load_config(tmp_path)


def test_top_level_not_mapping_raises(tmp_path):
    _write(tmp_path, "- just\n- a\n- list\n")
    with pytest.raises(SpecLintError, match="top level must be a mapping"):
        load_config(tmp_path)


def test_invalid_fail_on_raises(tmp_path):
    _write(tmp_path, "fail_on: maybe\n")
    with pytest.raises(SpecLintError, match="fail_on must be one of"):
        load_config(tmp_path)


def test_packages_not_list_of_strings_raises(tmp_path):
    _write(tmp_path, "packages: [1, 2, 3]\n")
    with pytest.raises(SpecLintError, match="`packages` must be a list of strings"):
        load_config(tmp_path)


def test_specs_wrong_type_raises(tmp_path):
    _write(tmp_path, "specs: oops\n")
    with pytest.raises(SpecLintError, match="`specs` must be a list of strings"):
        load_config(tmp_path)


def test_rules_not_mapping_raises(tmp_path):
    _write(tmp_path, "rules: [no-tbd]\n")
    with pytest.raises(SpecLintError, match="`rules` must be a mapping"):
        load_config(tmp_path)


def test_rule_value_wrong_type_raises(tmp_path):
    _write(tmp_path, """
    rules:
      no-tbd: 123
    """)
    with pytest.raises(SpecLintError, match="must be a string or mapping"):
        load_config(tmp_path)


def test_llm_not_mapping_raises(tmp_path):
    _write(tmp_path, "llm: api\n")
    with pytest.raises(SpecLintError, match="`llm` must be a mapping"):
        load_config(tmp_path)
