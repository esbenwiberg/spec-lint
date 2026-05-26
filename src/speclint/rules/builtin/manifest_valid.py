from __future__ import annotations

from typing import Any

from ...ir.types import SpecIR
from ..registry import rule
from ..types import ExpectedFinding, Finding, Fixture


_FIXTURES = [
    Fixture(
        name="valid-manifest-passes",
        files={
            "spec.yml": (
                "id: example\n"
                "status: accepted\n"
                "owner: esben\n"
                "references:\n"
                "  - src/example/**\n"
            ),
            "README.md": "# Example\n",
        },
        expects=(),
    ),
    Fixture(
        name="missing-spec-yml",
        files={
            "README.md": "# X\n",
        },
        expects=(ExpectedFinding(file="spec.yml", message_contains="Missing spec.yml"),),
    ),
    Fixture(
        name="invalid-yaml",
        files={
            "spec.yml": "id: x\nstatus: : : :\n  bad indent\n",
            "README.md": "# X\n",
        },
        expects=(ExpectedFinding(file="spec.yml", message_contains="invalid YAML"),),
    ),
    Fixture(
        name="unknown-status-value",
        files={
            "spec.yml": "id: x\nstatus: published\n",
            "README.md": "# X\n",
        },
        expects=(ExpectedFinding(file="spec.yml", message_contains="not in"),),
    ),
    Fixture(
        name="missing-required-fields",
        files={
            "spec.yml": "owner: esben\n",
            "README.md": "# X\n",
        },
        expects=(
            ExpectedFinding(file="spec.yml", message_contains="`id`"),
            ExpectedFinding(file="spec.yml", message_contains="`status`"),
        ),
    ),
    Fixture(
        name="references-must-be-list-of-strings",
        files={
            "spec.yml": "id: x\nstatus: accepted\nreferences: not-a-list\n",
            "README.md": "# X\n",
        },
        expects=(ExpectedFinding(file="spec.yml", message_contains="references"),),
    ),
]


@rule(
    id="manifest-valid",
    version="1.0.0",
    tier="static",
    default_severity="error",
    rationale=(
        "Each spec folder must contain a valid spec.yml with id and status. "
        "Without it the linter cannot apply status-based strictness."
    ),
    fixtures=_FIXTURES,
)
def check(ir: SpecIR, config: dict[str, Any]) -> list[Finding]:
    severity = config.get("severity", "error")
    findings: list[Finding] = []

    if ir.manifest is None and not ir.manifest_errors:
        findings.append(
            Finding(
                rule_id="manifest-valid",
                severity=severity,
                file="spec.yml",
                line=None,
                message="Missing spec.yml at spec folder root",
                hint="Create spec.yml with at least `id:` and `status:` fields.",
            )
        )
        return findings

    for err in ir.manifest_errors:
        findings.append(
            Finding(
                rule_id="manifest-valid",
                severity=severity,
                file="spec.yml",
                line=None,
                message=err,
                hint=None,
            )
        )
    return findings
