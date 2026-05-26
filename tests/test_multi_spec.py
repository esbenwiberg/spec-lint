"""Multi-spec runner — the seam where speclint stops being a rule engine
and becomes a real PR-validation linter.

These tests exercise the per-spec loop in `run()` against a repo containing
multiple spec folders simultaneously. The single-spec fixtures in
`test_fixtures.py` test rule logic in isolation; this file tests
*aggregation* — how findings, statuses, metadata, and Path B diffs fan
out across many specs in one run.
"""
from __future__ import annotations

from pathlib import Path

from speclint.config import Config, MetadataConfig, RuleConfig
from speclint.discovery import discover_specs
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

    Metadata sits in a sidecar (`meta.yml`) so callers must opt in via
    `MetadataConfig(sidecar="meta.yml")` to exercise status-aware /
    references-aware rules.
    """
    _write(repo, "specs/api/meta.yml",
           "status: accepted\nreferences:\n  - 'src/api/**'\n")
    _write(repo, "specs/api/README.md", "# api\n\nTBD: figure out auth.\n")

    _write(repo, "specs/auth/meta.yml",
           "status: draft\nreferences:\n  - 'src/auth/**'\n")
    _write(repo, "specs/auth/README.md", "# auth\n\nFIXME: still drafting.\n")

    _write(repo, "specs/billing/meta.yml",
           "status: implemented\nreferences:\n  - 'src/billing/**'\n")
    _write(repo, "specs/billing/README.md",
           "# billing\n\nThe system SHALL invoice monthly.\n")

    _write(repo, "src/api/router.py", "def get(): ...\n")
    _write(repo, "src/auth/login.py", "def login(): ...\n")
    _write(repo, "src/billing/invoice.py", "def bill(): ...\n")


def _cfg_with_sidecar() -> Config:
    cfg = Config()
    cfg.metadata = MetadataConfig(sidecar="meta.yml")
    return cfg


def test_discover_finds_all_three(tmp_path):
    _three_spec_repo(tmp_path)
    candidates = discover_specs(tmp_path)
    names = {c.name for c in candidates}
    assert names == {"api", "auth", "billing"}


def test_runner_visits_each_spec(tmp_path):
    _three_spec_repo(tmp_path)
    result = run(tmp_path, _cfg_with_sidecar())
    assert set(result.specs_checked) == {"api", "auth", "billing"}


def test_findings_are_tagged_with_correct_spec(tmp_path):
    _three_spec_repo(tmp_path)
    result = run(tmp_path, _cfg_with_sidecar())

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
    result = run(tmp_path, _cfg_with_sidecar())
    tbd_specs = {f.spec for f in result.findings if f.rule_id == "no-tbd"}
    assert tbd_specs == {"api"}, f"only api should fire no-tbd, got {tbd_specs}"


def test_path_b_fans_out_to_only_matching_specs(tmp_path):
    """One diff, three specs — refs-coupling must fire on exactly the specs
    whose `references` globs match a changed path."""
    _three_spec_repo(tmp_path)
    result = run(
        tmp_path,
        _cfg_with_sidecar(),
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
        _cfg_with_sidecar(),
        changed_paths=(
            "src/api/router.py", "specs/api/README.md",   # api spec co-changed
            "src/billing/invoice.py",                      # billing code only
        ),
    )
    coupling = {f.spec for f in result.findings if f.rule_id == "refs-coupling"}
    assert coupling == {"billing"}


def test_malformed_sidecar_does_not_block_other_specs(tmp_path):
    """Resilience: a malformed metadata sidecar in one folder must not
    crash the runner or suppress findings from sibling specs."""
    _three_spec_repo(tmp_path)
    # Corrupt the auth sidecar — invalid YAML
    _write(tmp_path, "specs/auth/meta.yml", "status: [unterminated\n")

    result = run(tmp_path, _cfg_with_sidecar())
    # All three were visited
    assert set(result.specs_checked) == {"api", "auth", "billing"}
    # api's no-tbd still fires — auth's broken metadata didn't spread
    assert any(
        f.spec == "api" and f.rule_id == "no-tbd" for f in result.findings
    )
    # auth's metadata is unreadable, so the draft skip can't apply →
    # no-tbd fires on auth too (FIXME marker)
    assert any(
        f.spec == "auth" and f.rule_id == "no-tbd" for f in result.findings
    )


def test_severity_count_aggregates_across_specs(tmp_path):
    _three_spec_repo(tmp_path)
    result = run(tmp_path, _cfg_with_sidecar())
    warns = [f for f in result.findings if f.severity == "warn"]
    # api alone has at least no-tbd (warn). Aggregate must be >= 1.
    assert len(warns) >= 1
    # exit_code_for considers the aggregate max severity
    assert exit_code_for(result, "warn") == 1
    assert exit_code_for(result, "error") == 0


def test_severity_override_applies_uniformly_across_specs(tmp_path):
    _three_spec_repo(tmp_path)
    cfg = _cfg_with_sidecar()
    cfg.rules["no-tbd"] = RuleConfig(severity="error")
    result = run(tmp_path, cfg)
    tbd = [f for f in result.findings if f.rule_id == "no-tbd"]
    assert tbd, "expected at least one no-tbd finding"
    assert all(f.severity == "error" for f in tbd), \
        "severity override should apply across every spec"


def test_specs_without_metadata_are_still_visited(tmp_path):
    """A folder under specs/*/ without the metadata sidecar still gets
    scanned; metadata-dependent rules just silently skip for it, and the
    run doesn't crash."""
    _three_spec_repo(tmp_path)
    _write(tmp_path, "specs/no-meta/README.md", "# orphan\n\nSHALL be linted.\n")

    result = run(tmp_path, _cfg_with_sidecar())
    assert "no-meta" in result.specs_checked
    # no metadata → refs-coupling and refs-resolve short-circuit, so no
    # findings from those rules on this spec.
    refs_findings = [
        f for f in result.findings
        if f.spec == "no-meta" and f.rule_id in {"refs-coupling", "refs-resolve"}
    ]
    assert refs_findings == []
