"""Every declared claim in a spec's metadata must be referenced by at
least one acceptance hook — otherwise the spec ships a promise that
nothing proves.

Mechanically: build a set of claim ids from ``claims_field`` and a set
of referenced ids from ``hooks_field``. Each claim id that doesn't
appear in the referenced set fires one finding.

Default field paths target the autopod contract shape
(``scenarios[*].id`` / ``required_facts[*].proves[*]``) because that's
what spec-writing skills emit today, but they're configurable for any
schema that splits claims from coverage entries by id.

This rule is sidecar-internal: it does not check whether referenced
artifacts exist on disk (``refs-resolve`` covers that) or whether the
diff touches them (``refs-coupling`` covers that). All three compose."""
from __future__ import annotations

import re
from typing import Any

from ...ir.types import SpecIR
from ..registry import rule
from ..types import ExpectedFinding, Finding, Fixture
from ._metadata import extract_strings


_FIXTURES = [
    Fixture(
        name="all-claims-have-hooks-passes",
        files={"README.md": "# x\n"},
        metadata={
            "scenarios": [
                {"id": "a"},
                {"id": "b"},
            ],
            "required_facts": [
                {"proves": ["a", "b"]},
            ],
        },
        expects=(),
    ),
    Fixture(
        name="single-unhooked-claim-fires",
        files={"README.md": "# x\n"},
        metadata={
            "scenarios": [
                {"id": "covered"},
                {"id": "orphan"},
            ],
            "required_facts": [
                {"proves": ["covered"]},
            ],
        },
        expects=(
            ExpectedFinding(
                file="contract.yaml",
                message_contains="orphan",
            ),
        ),
    ),
    Fixture(
        name="multiple-unhooked-claims-fire-separately",
        files={"README.md": "# x\n"},
        metadata={
            "scenarios": [
                {"id": "alpha"},
                {"id": "beta"},
                {"id": "gamma"},
            ],
            "required_facts": [
                {"proves": ["beta"]},
            ],
        },
        expects=(
            ExpectedFinding(message_contains="alpha"),
            ExpectedFinding(message_contains="gamma"),
        ),
    ),
    Fixture(
        name="no-required-facts-fires-all-claims",
        files={"README.md": "# x\n"},
        metadata={
            "scenarios": [
                {"id": "a"},
                {"id": "b"},
            ],
        },
        expects=(
            ExpectedFinding(message_contains="a"),
            ExpectedFinding(message_contains="b"),
        ),
    ),
    Fixture(
        name="no-metadata-no-findings",
        files={"README.md": "# x\n"},
        expects=(),
    ),
    Fixture(
        name="no-claims-field-no-findings",
        files={"README.md": "# x\n"},
        metadata={"title": "no scenarios here"},
        expects=(),
    ),
    Fixture(
        name="custom-field-paths",
        files={"README.md": "# x\n"},
        metadata={
            "acceptance_criteria": [
                {"key": "ac-1"},
                {"key": "ac-2"},
            ],
            "tests": [
                {"covers": ["ac-1"]},
            ],
        },
        options={
            "claims_field": "acceptance_criteria[*].key",
            "hooks_field": "tests[*].covers[*]",
        },
        expects=(
            ExpectedFinding(message_contains="ac-2"),
        ),
    ),
    Fixture(
        name="human-review-coverage-counts-by-default",
        files={"README.md": "# x\n"},
        metadata={
            "scenarios": [
                {"id": "automatable"},
                {"id": "needs-human-eye"},
            ],
            "required_facts": [
                {"proves": ["automatable"]},
            ],
            "human_review": [
                {"covers": ["needs-human-eye"]},
            ],
        },
        expects=(),
    ),
    Fixture(
        name="human-review-proves-variant-counts-too",
        files={"README.md": "# x\n"},
        metadata={
            "scenarios": [
                {"id": "needs-human-eye"},
            ],
            "human_review": [
                {"proves": ["needs-human-eye"]},
            ],
        },
        expects=(),
    ),
    Fixture(
        name="multi-path-hooks-field-aggregates",
        files={"README.md": "# x\n"},
        metadata={
            "scenarios": [{"id": "a"}, {"id": "b"}, {"id": "c"}],
            "unit_tests": [{"covers": ["a"]}],
            "integration_tests": [{"covers": ["b"]}],
        },
        options={
            "hooks_field": [
                "unit_tests[*].covers[*]",
                "integration_tests[*].covers[*]",
            ],
        },
        expects=(
            ExpectedFinding(message_contains="c"),
        ),
    ),
    Fixture(
        name="line-points-at-claim-declaration",
        files={"README.md": "# x\n"},
        metadata={
            "scenarios": [
                {"id": "first"},
                {"id": "lonely"},
            ],
            "required_facts": [
                {"proves": ["first"]},
            ],
        },
        expects=(
            ExpectedFinding(message_contains="lonely", line=6),
        ),
    ),
]


@rule(
    id="claims-have-hooks",
    version="1.0.0",
    tier="static",
    default_severity="warn",
    rationale=(
        "A claim with no acceptance hook is a promise nothing proves. "
        "When the sidecar lists scenarios/requirements separately from "
        "the artifacts that cover them, the join is mechanically "
        "checkable — every claim id should be referenced by at least "
        "one hook entry. Off unless metadata is configured."
    ),
    fixtures=_FIXTURES,
)
def check(ir: SpecIR, config: dict[str, Any]) -> list[Finding]:
    claims_field = config.get("claims_field", "scenarios[*].id")
    hooks_field = config.get(
        "hooks_field",
        [
            "required_facts[*].proves[*]",
            "human_review[*].proves[*]",
            "human_review[*].covers[*]",
        ],
    )

    claim_ids = extract_strings(ir.metadata, claims_field)
    if not claim_ids:
        return []

    hook_paths = [hooks_field] if isinstance(hooks_field, str) else list(hooks_field)
    hook_refs: set[str] = set()
    for path in hook_paths:
        hook_refs.update(extract_strings(ir.metadata, path))
    severity = config.get("severity", "warn")
    sidecar_name = _guess_sidecar(ir)
    sidecar_text = _read_sidecar_text(ir, sidecar_name)

    findings: list[Finding] = []
    seen: set[str] = set()
    for cid in claim_ids:
        if cid in seen:
            continue
        seen.add(cid)
        if cid in hook_refs:
            continue
        findings.append(
            Finding(
                rule_id="claims-have-hooks",
                severity=severity,
                file=sidecar_name,
                line=_locate_claim_line(sidecar_text, cid),
                message=f"Claim '{cid}' has no acceptance hook",
                hint=(
                    "Add a hook entry whose join field references this "
                    "claim id (e.g. `required_facts: [{proves: ["
                    f"{cid}]}}]`), or drop the claim if it's no longer "
                    "in scope."
                ),
            )
        )
    return findings


def _guess_sidecar(ir: SpecIR) -> str:
    try:
        for p in ir.folder.iterdir():
            if p.suffix in {".yml", ".yaml"} and p.is_file():
                return p.name
    except OSError:
        pass
    return "<metadata>"


def _read_sidecar_text(ir: SpecIR, name: str) -> str:
    if name == "<metadata>":
        return ""
    try:
        return (ir.folder / name).read_text(encoding="utf-8", errors="replace")
    except OSError:
        return ""


def _locate_claim_line(sidecar_text: str, claim_id: str) -> int | None:
    """Best-effort: find the line declaring this claim id in the sidecar.
    Matches ``id: <claim_id>`` (with or without quotes) — covers the
    common mapping-of-records shape. Returns ``None`` if we can't pin a
    line; the finding still surfaces, just without a line anchor."""
    if not sidecar_text or not claim_id:
        return None
    escaped = re.escape(claim_id)
    pattern = re.compile(
        rf"^\s*(?:-\s*)?id\s*:\s*[\"']?{escaped}[\"']?\s*(?:#.*)?$"
    )
    for i, line in enumerate(sidecar_text.splitlines(), start=1):
        if pattern.match(line):
            return i
    return None
