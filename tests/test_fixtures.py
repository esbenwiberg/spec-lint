"""Generic harness: every rule's fixtures run through one test path.

Adding a fixture to any rule is enough to extend coverage — no test code
needs to change.
"""
from __future__ import annotations

import pytest

from speclint.fixtures import assert_matches, run_fixture
from speclint.rules import load_rule_packages


def _all_rule_fixtures():
    """Yield (rule_id, fixture_name, rule, fixture) for every fixture of
    every rule in the default package."""
    reg = load_rule_packages(["default"])
    for rule in reg.all():
        for fx in rule.fixtures:
            yield pytest.param(rule, fx, id=f"{rule.id}::{fx.name}")


@pytest.mark.parametrize("rule,fixture", list(_all_rule_fixtures()))
def test_rule_fixture(rule, fixture, tmp_path):
    findings = run_fixture(rule, fixture, tmp_path)
    assert_matches(rule.id, fixture, findings)


def test_every_rule_has_at_least_one_fixture():
    reg = load_rule_packages(["default"])
    rules_without = [r.id for r in reg.all() if not r.fixtures]
    assert not rules_without, f"rules without fixtures: {rules_without}"


def test_every_rule_has_at_least_one_pass_and_one_fail_fixture():
    reg = load_rule_packages(["default"])
    missing = []
    for r in reg.all():
        has_pass = any(fx.expects_no_findings for fx in r.fixtures)
        has_fail = any(not fx.expects_no_findings for fx in r.fixtures)
        if not (has_pass and has_fail):
            missing.append(
                f"{r.id} (pass={has_pass}, fail={has_fail})"
            )
    assert not missing, f"rules missing pass+fail fixtures: {missing}"
