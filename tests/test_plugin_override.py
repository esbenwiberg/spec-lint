"""Cross-package override semantics — real plugin, not self-loaded default.

Exercises `load_rule_packages(["default", "testplugin"])` against the
in-tree `speclint-test-plugin` package installed editable from
`tests/plugin_pkg/`. Covers:

  - Last-package-wins replacement (default `no-tbd` → testplugin `no-tbd`)
  - Override audit log records the real cross-package event
  - Package-order reversal flips precedence
  - Plugin-only rule (`frontmatter-owner`) loads alongside builtins
  - Plugin rule's own fixtures still pass through the harness
"""
from __future__ import annotations

import pytest

from speclint.fixtures import assert_matches, run_fixture
from speclint.ir import build_spec_ir
from speclint.rules import load_rule_packages


def test_plugin_overrides_default_no_tbd():
    """testplugin's no-tbd (v2.0.0, error severity, XXX-only) replaces the
    default's no-tbd (v1.0.0, warn, TBD/TODO/FIXME)."""
    reg = load_rule_packages(["default", "testplugin"])
    no_tbd = reg.rules["no-tbd"]
    assert no_tbd.package == "testplugin"
    assert no_tbd.version == "2.0.0"
    assert no_tbd.default_severity == "error"


def test_override_audit_records_cross_package_replacement():
    reg = load_rule_packages(["default", "testplugin"])
    no_tbd_overrides = [o for o in reg.overrides if o.rule_id == "no-tbd"]
    assert len(no_tbd_overrides) == 1
    event = no_tbd_overrides[0]
    assert event.from_package == "default"
    assert event.to_package == "testplugin"


def test_reverse_order_flips_precedence():
    """Default wins when loaded last."""
    reg = load_rule_packages(["testplugin", "default"])
    assert reg.rules["no-tbd"].package == "default"
    assert reg.rules["no-tbd"].version == "1.0.0"
    # frontmatter-owner only exists in testplugin — still present
    assert "frontmatter-owner" in reg.rules
    assert reg.rules["frontmatter-owner"].package == "testplugin"


def test_plugin_no_tbd_fires_on_xxx_not_tbd(tmp_path):
    """Proves the override actually swapped the check function — the
    builtin version would fire on TBD; the plugin version ignores it."""
    reg = load_rule_packages(["default", "testplugin"])
    (tmp_path / "spec.yml").write_text("id: x\nstatus: accepted\n")
    (tmp_path / "README.md").write_text("XXX is bad\nTBD is fine here\n")
    ir = build_spec_ir(tmp_path)

    findings = reg.rules["no-tbd"].check(ir, {})
    messages = [f.message for f in findings]
    assert any("XXX" in m for m in messages), messages
    assert not any("TBD" in m for m in messages), messages
    # Severity comes from the plugin's default
    assert all(f.severity == "error" for f in findings)


def test_frontmatter_owner_rule_loads_from_plugin():
    reg = load_rule_packages(["default", "testplugin"])
    assert "frontmatter-owner" in reg.rules
    rule = reg.rules["frontmatter-owner"]
    assert rule.package == "testplugin"
    assert rule.default_severity == "warn"


def _plugin_fixtures():
    """Yield (rule, fixture) for every fixture shipped by the testplugin
    package — loaded standalone so plugin rules win regardless of
    builtin-shadowing in the merged registry."""
    reg = load_rule_packages(["testplugin"])
    for rule in reg.all():
        for fx in rule.fixtures:
            yield pytest.param(rule, fx, id=f"{rule.id}::{fx.name}")


@pytest.mark.parametrize("rule,fixture", list(_plugin_fixtures()))
def test_plugin_rule_fixtures(rule, fixture, tmp_path):
    findings = run_fixture(rule, fixture, tmp_path)
    assert_matches(rule.id, fixture, findings)


def test_unknown_package_raises_helpful_error():
    from speclint.rules.registry import SpecLintError

    with pytest.raises(SpecLintError, match="not found via entry-points"):
        load_rule_packages(["does-not-exist"])
