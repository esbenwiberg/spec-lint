"""IR builder tests against the in-repo example spec.

`example-spec/` is a multi-file spec with no sidecar metadata loaded
(the test driver doesn't configure one). The builder must still parse
headings/claims/links/terms cleanly.
"""
from pathlib import Path

from speclint.discovery import SpecCandidate, discover_specs
from speclint.ir import build_spec_ir

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


def test_discover_finds_example_spec():
    candidates = discover_specs(REPO)
    folders = {c.folder for c in candidates}
    assert SPEC in folders


def test_metadata_empty_when_no_sidecar_configured():
    """No `metadata_sidecar` kwarg = no metadata loaded, regardless of
    what files sit alongside the markdown."""
    ir = build_spec_ir(_candidate_for(SPEC))
    assert ir.metadata == {}
    assert ir.metadata_errors == []


def test_files_collected():
    ir = build_spec_ir(_candidate_for(SPEC))
    assert "README.md" in ir.files
    assert "api.md" in ir.files


def test_headings_extracted():
    ir = build_spec_ir(_candidate_for(SPEC))
    titles = {h.text for h in ir.headings}
    assert "Example Spec" in titles
    assert "Requirements" in titles
    assert "API Surface" in titles


def test_claims_extracted_with_modals():
    ir = build_spec_ir(_candidate_for(SPEC))
    modals = {c.modal for c in ir.claims if c.modal}
    assert "MUST" in modals
    assert "SHALL" in modals
    assert "SHOULD" in modals


def test_links_extracted():
    ir = build_spec_ir(_candidate_for(SPEC))
    internal = [link for link in ir.links if link.is_internal]
    assert any(link.target == "./api.md" for link in internal)


def test_verification_hook_detected():
    ir = build_spec_ir(_candidate_for(SPEC))
    readme_claims = [c for c in ir.claims if c.file == "README.md"]
    assert any(c.has_verification_hook for c in readme_claims)
