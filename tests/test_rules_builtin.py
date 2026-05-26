"""Smoke tests for the default rule package — entry-point loading and
end-to-end behavior against the in-repo example spec. Per-rule behavior
is exercised by inline fixtures in `tests/test_fixtures.py`.
"""
from pathlib import Path

from speclint.discovery import SpecCandidate
from speclint.ir import build_spec_ir
from speclint.rules import load_rule_packages

REPO = Path(__file__).resolve().parent.parent
SPEC = REPO / "specs" / "example-spec"


def _candidate_for(folder: Path) -> SpecCandidate:
    md_files = tuple(sorted(p for p in folder.iterdir() if p.suffix == ".md"))
    return SpecCandidate(
        name=folder.name,
        folder=folder,
        md_files=md_files,
        is_single_file=False,
    )


def test_registry_loads_default_package_via_entry_points():
    reg = load_rule_packages(["default"])
    ids = set(reg.rules.keys())
    # Core static rules always ship in the default package.
    assert {"no-tbd", "no-weasel-words", "refs-resolve", "refs-coupling"} <= ids
    for rule in reg.all():
        assert rule.package == "default"


def test_default_package_runs_on_example_spec():
    """End-to-end: every default rule executes against the example spec
    without raising. Per-rule semantics are checked by fixtures."""
    reg = load_rule_packages(["default"])
    ir = build_spec_ir(_candidate_for(SPEC))
    for rule in reg.all():
        findings = rule.check(ir, {})
        assert isinstance(findings, list)
