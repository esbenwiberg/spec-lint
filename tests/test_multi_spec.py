"""Multi-spec runner — the seam where speclint stops being a rule engine
and becomes a real PR-validation linter.

These tests exercise the per-spec loop in `run()` against a repo containing
multiple spec folders simultaneously. The single-spec fixtures in
`test_fixtures.py` test rule logic in isolation; this file tests
*aggregation* — how findings, statuses, manifests, and Path B diffs fan
out across many specs in one run.
"""
from __future__ import annotations

from pathlib import Path

from speclint.config import Config, RuleConfig
from speclint.ir import discover_spec_folders
from speclint.runner import exit_code_for, run


def _write(repo: Path, rel: str, content: str) -> None:
    p = repo / rel
    p.parent.mkdir(parents=True, exist_ok=True)
    p.write_text(content, encoding="utf-8")


def _three_spec_repo(repo: Path) -> None:
    """Lay down a realistic 3-spec repo:
      - api/      : accepted, references src/api/**, has TBD
      - auth/     : draft, has TBD (should be skipped by no-tbd)
      - billing/  : implemented, references src/billing/**, clean
    """
    _write(repo, "specs/api/spec.yml",
           "id: api\nstatus: accepted\nreferences:\n  - 'src/api/**'\n")
    _write(repo, "specs/api/README.md", "# api\n\nTBD: figure out auth.\n")

    _write(repo, "specs/auth/spec.yml",
           "id: auth\nstatus: draft\nreferences:\n  - 'src/auth/**'\n")
    _write(repo, "specs/auth/README.md", "# auth\n\nFIXME: still drafting.\n")

    _write(repo, "specs/billing/spec.yml",
           "id: billing\nstatus: implemented\nreferences:\n  - 'src/billing/**'\n")
    _write(repo, "specs/billing/README.md",
           "# billing\n\nThe system SHALL invoice monthly.\n")

    _write(repo, "src/api/router.py", "def get(): ...\n")
    _write(repo, "src/auth/login.py", "def login(): ...\n")
    _write(repo, "src/billing/invoice.py", "def bill(): ...\n")


def test_discover_finds_all_three(tmp_path):
    _three_spec_repo(tmp_path)
    folders = discover_spec_folders(tmp_path, ["specs/*/"])
    names = {f.name for f in folders}
    assert names == {"api", "auth", "billing"}


def test_runner_visits_each_spec(tmp_path):
    _three_spec_repo(tmp_path)
    result = run(tmp_path, Config())
    assert set(result.specs_checked) == {"api", "auth", "billing"}


def test_findings_are_tagged_with_correct_spec(tmp_path):
    _three_spec_repo(tmp_path)
    result = run(tmp_path, Config())

    by_spec: dict[str, list[str]] = {}
    for f in result.findings:
        by_spec.setdefault(f.spec or "?", []).append(f.rule_id)

    # api has a TBD on an accepted spec → no-tbd fires
    assert "no-tbd" in by_spec.get("api", [])
    # auth is draft → no-tbd short-circuits, must NOT fire
    assert "no-tbd" not in by_spec.get("auth", [])
    # billing is clean → no no-tbd
    assert "no-tbd" not in by_spec.get("billing", [])


def test_draft_status_isolates_one_spec_not_others(tmp_path):
    """Regression guard: a draft spec must not silence rules on other specs."""
    _three_spec_repo(tmp_path)
    result = run(tmp_path, Config())
    tbd_specs = {f.spec for f in result.findings if f.rule_id == "no-tbd"}
    assert tbd_specs == {"api"}, f"only api should fire no-tbd, got {tbd_specs}"


def test_path_b_fans_out_to_only_matching_specs(tmp_path):
    """One diff, three specs — refs-coupling must fire on exactly the specs
    whose `references` globs match a changed path."""
    _three_spec_repo(tmp_path)
    result = run(
        tmp_path,
        Config(),
        changed_paths=("src/api/router.py", "src/billing/invoice.py"),
    )
    coupling = {f.spec for f in result.findings if f.rule_id == "refs-coupling"}
    # api: changed under references, spec folder not touched → fires
    # billing: same → fires
    # auth: no change under references → no fire
    assert coupling == {"api", "billing"}


def test_co_change_silences_one_spec_only(tmp_path):
    """If only api/'s spec folder is touched in the diff, api is silenced
    but billing still fires (its code changed but its spec didn't)."""
    _three_spec_repo(tmp_path)
    result = run(
        tmp_path,
        Config(),
        changed_paths=(
            "src/api/router.py", "specs/api/README.md",   # api spec co-changed
            "src/billing/invoice.py",                      # billing code only
        ),
    )
    coupling = {f.spec for f in result.findings if f.rule_id == "refs-coupling"}
    assert coupling == {"billing"}


def test_one_specs_bad_manifest_does_not_block_others(tmp_path):
    """Resilience: a malformed spec.yml in one folder must not crash the
    runner or suppress findings from sibling specs."""
    _three_spec_repo(tmp_path)
    # Corrupt the auth manifest
    _write(tmp_path, "specs/auth/spec.yml", "id: auth\nstatus: lolwat\n")

    result = run(tmp_path, Config())
    # All three were visited
    assert set(result.specs_checked) == {"api", "auth", "billing"}
    # manifest-valid fires for auth specifically
    bad_manifest = [
        f for f in result.findings
        if f.rule_id == "manifest-valid" and f.spec == "auth"
    ]
    assert bad_manifest
    # api's no-tbd still fires — auth's manifest issue didn't spread
    assert any(
        f.spec == "api" and f.rule_id == "no-tbd" for f in result.findings
    )


def test_severity_count_aggregates_across_specs(tmp_path):
    _three_spec_repo(tmp_path)
    result = run(tmp_path, Config())
    warns = [f for f in result.findings if f.severity == "warn"]
    # api alone has at least no-tbd (warn). Aggregate must be >= 1.
    assert len(warns) >= 1
    # exit_code_for considers the aggregate max severity
    assert exit_code_for(result, "warn") == 1
    assert exit_code_for(result, "error") == 0


def test_severity_override_applies_uniformly_across_specs(tmp_path):
    _three_spec_repo(tmp_path)
    cfg = Config()
    cfg.rules["no-tbd"] = RuleConfig(severity="error")
    result = run(tmp_path, cfg)
    tbd = [f for f in result.findings if f.rule_id == "no-tbd"]
    assert tbd, "expected at least one no-tbd finding"
    assert all(f.severity == "error" for f in tbd), \
        "severity override should apply across every spec"


def test_specs_with_no_manifest_are_still_visited(tmp_path):
    """A folder under specs/*/ without spec.yml still gets scanned;
    manifest-valid fires for it but the run doesn't crash."""
    _three_spec_repo(tmp_path)
    _write(tmp_path, "specs/no-manifest/README.md", "# orphan\n")

    result = run(tmp_path, Config())
    assert "no-manifest" in result.specs_checked
    missing = [
        f for f in result.findings
        if f.spec == "no-manifest" and f.rule_id == "manifest-valid"
    ]
    assert missing
