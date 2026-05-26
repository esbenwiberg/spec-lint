"""Semantic-tier rule: flag pairs of headings within a spec whose
embeddings are nearly identical — likely duplicate sections that should
be merged.

Demonstrates the Tier 2 pattern: read embeddings from `ir.embedder`,
compute pairwise cosine similarity, fire on threshold breach. Fully
deterministic given a fixed embedder.

Fixtures use BagOfTokensEmbedder (token overlap). Production uses
FastembedEmbedder (BAAI/bge-small-en-v1.5). The rule logic is identical;
only the threshold may need tuning per backend."""
from __future__ import annotations

from typing import Any

from ...ir.types import SpecIR
from ...semantic import cosine_sim
from ..registry import rule
from ..types import ExpectedFinding, Finding, Fixture


_FIXTURES = [
    Fixture(
        name="distinct-headings-pass",
        files={
            "spec.yml": "id: x\nstatus: accepted\n",
            "README.md": (
                "# API\n"
                "## Authentication\n"
                "## Rate Limiting\n"
                "## Error Responses\n"
                "## Pagination\n"
            ),
        },
        expects=(),
    ),
    Fixture(
        name="duplicate-headings-fire",
        files={
            "spec.yml": "id: x\nstatus: accepted\n",
            "README.md": (
                "# Service\n"
                "## API Rate Limiting\n"
                "## Pagination\n"
                "## API Rate Limiting Strategy\n"
            ),
        },
        # Two headings share three of four/five tokens — cosine sim ~0.87
        expects=(
            ExpectedFinding(
                file="README.md",
                message_contains="API Rate Limiting",
            ),
        ),
    ),
    Fixture(
        name="case-and-order-invariant",
        files={
            "spec.yml": "id: x\nstatus: accepted\n",
            "README.md": (
                "# Spec\n"
                "## PAYMENT PROCESSING\n"
                "## Logging\n"
                "## processing payment\n"  # same tokens, different order/case
            ),
        },
        expects=(
            ExpectedFinding(message_contains="PAYMENT PROCESSING"),
        ),
    ),
    Fixture(
        name="single-heading-no-pairs",
        files={
            "spec.yml": "id: x\nstatus: accepted\n",
            "README.md": "# Only One\n",
        },
        expects=(),
    ),
    Fixture(
        name="no-headings-no-findings",
        files={
            "spec.yml": "id: x\nstatus: accepted\n",
            "README.md": "Just a paragraph, no headings.\n",
        },
        expects=(),
    ),
    Fixture(
        name="custom-threshold-can-loosen",
        files={
            "spec.yml": "id: x\nstatus: accepted\n",
            "README.md": (
                "# Spec\n"
                "## API Rate Limiting\n"
                "## API Rate Limiting Strategy\n"
            ),
        },
        # Raise threshold above what the duplicate pair scores — must
        # silence the finding.
        options={"threshold": 0.999},
        expects=(),
    ),
]


@rule(
    id="heading-redundancy",
    version="1.0.0",
    tier="semantic",
    default_severity="info",
    rationale=(
        "Two headings whose embeddings are nearly identical usually mean "
        "the same concept is documented twice — readers lose track of "
        "which section is canonical. Merge them or rename one."
    ),
    fixtures=_FIXTURES,
)
def check(ir: SpecIR, config: dict[str, Any]) -> list[Finding]:
    if ir.embedder is None or len(ir.headings) < 2:
        return []

    threshold = float(config.get("threshold", 0.85))
    severity = config.get("severity", "info")

    texts = [h.text for h in ir.headings]
    vectors = ir.embedder.embed(texts)

    findings: list[Finding] = []
    seen: set[tuple[int, int]] = set()
    for i in range(len(ir.headings)):
        for j in range(i + 1, len(ir.headings)):
            sim = cosine_sim(vectors[i], vectors[j])
            if sim < threshold:
                continue
            # Same file? Only flag intra-file redundancy — cross-file
            # near-duplicates are usually intentional (overviews etc).
            hi = ir.headings[i]
            hj = ir.headings[j]
            if hi.file != hj.file:
                continue
            key = (i, j)
            if key in seen:
                continue
            seen.add(key)
            findings.append(
                Finding(
                    rule_id="heading-redundancy",
                    severity=severity,
                    file=hi.file,
                    line=hi.line,
                    message=(
                        f"Heading {hi.text!r} (line {hi.line}) is "
                        f"~{sim:.2f} similar to {hj.text!r} (line {hj.line}) "
                        f"— possible duplicate section."
                    ),
                    hint="Merge the sections, or rename one to disambiguate.",
                )
            )
    return findings
