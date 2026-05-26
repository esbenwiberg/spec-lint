"""Custom plugin rule: each spec.yml must declare an owner. Demonstrates a
team adding a rule on top of the default package without overriding it."""
from __future__ import annotations

from typing import Any

from speclint.ir.types import SpecIR
from speclint.rules.registry import rule
from speclint.rules.types import ExpectedFinding, Finding, Fixture


_FIXTURES = [
    Fixture(
        name="owner-present-passes",
        files={
            "spec.yml": "id: x\nstatus: accepted\nowner: esben\n",
            "README.md": "# X\n",
        },
        expects=(),
    ),
    Fixture(
        name="owner-missing-fires",
        files={
            "spec.yml": "id: x\nstatus: accepted\n",
            "README.md": "# X\n",
        },
        expects=(ExpectedFinding(file="spec.yml", message_contains="owner"),),
    ),
]


@rule(
    id="frontmatter-owner",
    version="1.0.0",
    tier="static",
    default_severity="warn",
    rationale="Specs must name an owner so reviewers know who to ping.",
    fixtures=_FIXTURES,
)
def check(ir: SpecIR, config: dict[str, Any]) -> list[Finding]:
    if ir.manifest is None:
        return []
    if not ir.manifest.owner:
        return [
            Finding(
                rule_id="frontmatter-owner",
                severity=config.get("severity", "warn"),
                file="spec.yml",
                line=None,
                message="Missing required field `owner`",
                hint="Add `owner: <github-handle>` to spec.yml.",
            )
        ]
    return []
