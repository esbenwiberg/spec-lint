from pathlib import Path

from speclint.ir import build_spec_ir, discover_spec_folders

REPO = Path(__file__).resolve().parent.parent
SPEC = REPO / "specs" / "example-spec"


def test_discover_finds_example_spec():
    folders = discover_spec_folders(REPO, ["specs/*/"])
    assert SPEC in folders


def test_manifest_loaded():
    ir = build_spec_ir(SPEC)
    assert ir.manifest is not None
    assert ir.manifest.id == "example-spec"
    assert ir.manifest.status == "accepted"
    assert ir.manifest.references == ("src/example/**",)
    assert ir.manifest_errors == []


def test_files_collected():
    ir = build_spec_ir(SPEC)
    assert "README.md" in ir.files
    assert "api.md" in ir.files


def test_headings_extracted():
    ir = build_spec_ir(SPEC)
    titles = {h.text for h in ir.headings}
    assert "Example Spec" in titles
    assert "Requirements" in titles
    assert "API Surface" in titles


def test_claims_extracted_with_modals():
    ir = build_spec_ir(SPEC)
    modals = {c.modal for c in ir.claims if c.modal}
    assert "MUST" in modals
    assert "SHALL" in modals
    assert "SHOULD" in modals


def test_links_extracted():
    ir = build_spec_ir(SPEC)
    internal = [link for link in ir.links if link.is_internal]
    assert any(link.target == "./api.md" for link in internal)


def test_verification_hook_detected():
    ir = build_spec_ir(SPEC)
    # README has an Acceptance heading so all claims should be hooked
    readme_claims = [c for c in ir.claims if c.file == "README.md"]
    assert any(c.has_verification_hook for c in readme_claims)
