"""Semantic-tier rule: flag pairs of MUST/SHALL/SHOULD/MAY claims within
the same file whose embeddings are nearly identical — the same
requirement stated twice, which lets one drift out of sync.

Mirrors heading-redundancy but operates on `ir.claims`. Intra-file only —
cross-file near-duplicate claims are usually intentional (an overview
restating a detail).

Fixtures use BagOfTokensEmbedder (token overlap). Production uses
FastembedEmbedder. Threshold may need per-backend tuning."""
from __future__ import annotations

from typing import Any

from ...ir.types import SpecIR
from ...semantic import cosine_sim
from ..registry import rule
from ..types import ExpectedFinding, Finding, Fixture


_FIXTURES = [
    Fixture(
        name="distinct-claims-pass",
        files={
            "spec.yml": "id: x\nstatus: accepted\n",
            "README.md": (
                "# Spec\n"
                "- The system MUST authenticate users on login.\n"
                "- Errors SHOULD be reported to the ops channel.\n"
                "- Payments MUST be idempotent across retries.\n"
            ),
        },
        expects=(),
    ),
    Fixture(
        name="duplicate-claims-fire",
        files={
            "spec.yml": "id: x\nstatus: accepted\n",
            "README.md": (
                "# Spec\n"
                "- The system MUST rate-limit API requests.\n"
                "- Audit logs SHOULD be retained for 90 days.\n"
                "- The system MUST limit API rate requests.\n"
            ),
        },
        # The two rate-limit claims share the same token set — cosine 1.0.
        expects=(
            ExpectedFinding(
                file="README.md",
                message_contains="rate",
            ),
        ),
    ),
    Fixture(
        name="cross-file-not-flagged",
        files={
            "spec.yml": "id: x\nstatus: accepted\n",
            "overview.md": (
                "# Overview\n"
                "- The system MUST rate-limit API requests.\n"
            ),
            "details.md": (
                "# Details\n"
                "- The system MUST rate-limit API requests.\n"
            ),
        },
        # Same claim text, different files — intentional restatement.
        expects=(),
    ),
    Fixture(
        name="single-claim-no-pairs",
        files={
            "spec.yml": "id: x\nstatus: accepted\n",
            "README.md": "# Spec\n- The system MUST log audit events.\n",
        },
        expects=(),
    ),
    Fixture(
        name="no-claims-no-findings",
        files={
            "spec.yml": "id: x\nstatus: accepted\n",
            "README.md": "# Spec\nNo requirements here, just prose.\n",
        },
        expects=(),
    ),
    Fixture(
        name="custom-threshold-can-loosen",
        files={
            "spec.yml": "id: x\nstatus: accepted\n",
            "README.md": (
                "# Spec\n"
                "- The system MUST rate-limit API requests.\n"
                "- The system MUST limit API rate requests.\n"
            ),
        },
        # Raise threshold above the cosine score — must silence.
        options={"threshold": 1.01},
        expects=(),
    ),
]


@rule(
    id="claim-redundancy",
    version="1.0.0",
    tier="semantic",
    default_severity="info",
    rationale=(
        "Two MUST/SHALL claims in the same file with near-identical "
        "embeddings usually mean the same requirement was written twice. "
        "When one drifts and the other doesn't, the spec contradicts "
        "itself. Merge them or rephrase one to make the distinction clear."
    ),
    fixtures=_FIXTURES,
)
def check(ir: SpecIR, config: dict[str, Any]) -> list[Finding]:
    if ir.embedder is None or len(ir.claims) < 2:
        return []

    threshold = float(config.get("threshold", 0.85))
    severity = config.get("severity", "info")

    texts = [c.text for c in ir.claims]
    vectors = ir.embedder.embed(texts)

    findings: list[Finding] = []
    seen: set[tuple[int, int]] = set()
    for i in range(len(ir.claims)):
        for j in range(i + 1, len(ir.claims)):
            ci = ir.claims[i]
            cj = ir.claims[j]
            if ci.file != cj.file:
                continue
            sim = cosine_sim(vectors[i], vectors[j])
            if sim < threshold:
                continue
            key = (i, j)
            if key in seen:
                continue
            seen.add(key)
            findings.append(
                Finding(
                    rule_id="claim-redundancy",
                    severity=severity,
                    file=ci.file,
                    line=ci.line,
                    message=(
                        f"Claim {_snippet(ci.text)!r} (line {ci.line}) is "
                        f"~{sim:.2f} similar to {_snippet(cj.text)!r} "
                        f"(line {cj.line}) — possible duplicate requirement."
                    ),
                    hint="Merge the claims, or rephrase one to disambiguate.",
                )
            )
    return findings


def _snippet(text: str, limit: int = 80) -> str:
    """Trim a claim to a one-line snippet for inclusion in finding messages."""
    flat = " ".join(text.split())
    if len(flat) <= limit:
        return flat
    return flat[: limit - 1] + "…"
