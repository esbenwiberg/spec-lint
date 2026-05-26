"""`.speclint.yml` loading — full coverage of `load_config` branches."""
from __future__ import annotations

import textwrap

import pytest

from speclint.config import LLMConfig, MetadataConfig, RuleConfig, load_config
from speclint.discovery import DEFAULT_ROOTS
from speclint.rules.registry import SpecLintError


def _write(tmp_path, content: str):
    (tmp_path / ".speclint.yml").write_text(textwrap.dedent(content), encoding="utf-8")


def test_no_config_returns_defaults(tmp_path):
    cfg = load_config(tmp_path)
    assert cfg.packages == ["default"]
    assert cfg.roots == list(DEFAULT_ROOTS)
    assert cfg.extra_roots == []
    assert cfg.ignore == []
    assert cfg.fail_on == "error"
    assert cfg.rules == {}
    assert isinstance(cfg.llm, LLMConfig)
    assert isinstance(cfg.metadata, MetadataConfig)
    assert cfg.metadata.sidecar is None


def test_empty_yaml_returns_defaults(tmp_path):
    _write(tmp_path, "")
    cfg = load_config(tmp_path)
    assert cfg.packages == ["default"]


def test_full_top_level_fields(tmp_path):
    _write(tmp_path, """
    packages: [default, my-team]
    roots: ["docs/specs", "specs"]
    extra_roots: ["adrs"]
    ignore: ["**/draft/*"]
    fail_on: warn
    """)
    cfg = load_config(tmp_path)
    assert cfg.packages == ["default", "my-team"]
    assert cfg.roots == ["docs/specs", "specs"]
    assert cfg.extra_roots == ["adrs"]
    assert cfg.ignore == ["**/draft/*"]
    assert cfg.fail_on == "warn"


def test_metadata_sidecar_parsed(tmp_path):
    _write(tmp_path, """
    metadata:
      sidecar: contract.yaml
    """)
    cfg = load_config(tmp_path)
    assert cfg.metadata.sidecar == "contract.yaml"


def test_metadata_block_with_explicit_null_sidecar(tmp_path):
    _write(tmp_path, """
    metadata:
      sidecar: null
    """)
    cfg = load_config(tmp_path)
    assert cfg.metadata.sidecar is None


def test_metadata_not_mapping_raises(tmp_path):
    _write(tmp_path, "metadata: contract.yaml\n")
    with pytest.raises(SpecLintError, match="`metadata` must be a mapping"):
        load_config(tmp_path)


def test_metadata_sidecar_wrong_type_raises(tmp_path):
    _write(tmp_path, """
    metadata:
      sidecar: 42
    """)
    with pytest.raises(SpecLintError, match="`metadata.sidecar` must be a string"):
        load_config(tmp_path)


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


def test_roots_wrong_type_raises(tmp_path):
    _write(tmp_path, "roots: oops\n")
    with pytest.raises(SpecLintError, match="`roots` must be a list of strings"):
        load_config(tmp_path)


def test_extra_roots_wrong_type_raises(tmp_path):
    _write(tmp_path, "extra_roots: [1, 2]\n")
    with pytest.raises(SpecLintError, match="`extra_roots` must be a list of strings"):
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
