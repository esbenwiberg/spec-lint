"""Fixture harness for rule tests.

Each rule ships a list of Fixture instances. ``run_fixture`` materializes
a fixture into a tmp directory, builds the SpecIR, runs the rule, and
returns the findings — ready for the caller to assert against
``fixture.expects``.

If a fixture declares ``metadata``, the harness writes it to a sidecar
YAML file (default ``contract.yaml``) inside the spec folder and tells
the IR builder to load it. That exercises the same code path the
runner uses in production, with no schema imposed on the fixture.
"""
from __future__ import annotations

from pathlib import Path
from typing import Any

import yaml

from .discovery import SpecCandidate
from .ir import build_spec_ir
from .rules.types import ExpectedFinding, Finding, Fixture, Rule
from .semantic import BagOfTokensEmbedder


def materialize(fixture: Fixture, tmp_path: Path) -> tuple[Path, Path]:
    """Write fixture into tmp_path. Returns ``(spec_folder, repo_root)``.

    Layout: ``tmp_path`` is the repo root, the spec lives at
    ``tmp_path/spec/``. Spec files (``fixture.files``) go under the spec
    folder; ``repo_files`` go at repo root.
    """
    repo_root = tmp_path
    spec_folder = tmp_path / "spec"
    spec_folder.mkdir(parents=True, exist_ok=True)

    for rel, content in fixture.files.items():
        full = spec_folder / rel
        full.parent.mkdir(parents=True, exist_ok=True)
        full.write_text(content, encoding="utf-8")

    for rel, content in fixture.repo_files.items():
        full = repo_root / rel
        full.parent.mkdir(parents=True, exist_ok=True)
        full.write_text(content, encoding="utf-8")

    if fixture.metadata:
        (spec_folder / fixture.sidecar_filename).write_text(
            yaml.safe_dump(fixture.metadata), encoding="utf-8"
        )

    return spec_folder, repo_root


def run_fixture(rule: Rule, fixture: Fixture, tmp_path: Path,
                config_overrides: dict[str, Any] | None = None) -> list[Finding]:
    """Materialize, build IR, run rule. Returns findings.

    Semantic-tier rules get a deterministic BagOfTokensEmbedder injected so
    fixtures don't require fastembed installed. Production runs use the
    real FastembedEmbedder via the runner."""
    spec_folder, repo_root = materialize(fixture, tmp_path)
    embedder = BagOfTokensEmbedder() if rule.tier == "semantic" else None

    md_files = tuple(sorted(p for p in spec_folder.iterdir() if p.suffix == ".md"))
    candidate = SpecCandidate(
        name=spec_folder.name,
        folder=spec_folder,
        md_files=md_files,
        is_single_file=False,
    )

    ir = build_spec_ir(
        candidate,
        repo_root=repo_root,
        changed_paths=fixture.changed_paths,
        embedder=embedder,
        metadata_sidecar=fixture.sidecar_filename if fixture.metadata else None,
    )
    opts: dict[str, Any] = dict(fixture.options)
    if config_overrides:
        opts.update(config_overrides)
    return rule.check(ir, opts)


def assert_matches(rule_id: str, fixture: Fixture, findings: list[Finding]) -> None:
    """Assert findings match fixture.expects. Raises AssertionError with a
    readable diff on failure."""
    if fixture.expects_no_findings:
        if findings:
            rendered = "\n".join(f"  {f.severity} {f.file}:{f.line} {f.message}" for f in findings)
            raise AssertionError(
                f"[{rule_id}:{fixture.name}] expected zero findings, got {len(findings)}:\n{rendered}"
            )
        return

    for expected in fixture.expects:
        if not any(_matches(expected, f) for f in findings):
            rendered = (
                "\n".join(f"  {f.severity} {f.file}:{f.line} {f.message}" for f in findings)
                or "  (no findings)"
            )
            raise AssertionError(
                f"[{rule_id}:{fixture.name}] no finding matched {expected!r}; got:\n{rendered}"
            )


def _matches(expected: ExpectedFinding, finding: Finding) -> bool:
    if expected.line is not None and expected.line != finding.line:
        return False
    if expected.file is not None and expected.file != finding.file:
        return False
    if expected.severity is not None and expected.severity != finding.severity:
        return False
    if expected.message_contains is not None and expected.message_contains not in finding.message:
        return False
    if expected.has_fix is not None and expected.has_fix != (finding.fix is not None):
        return False
    return True
