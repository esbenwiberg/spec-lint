"""Custom plugin rule: every spec's metadata sidecar must declare an
``owner`` field. Demonstrates a team adding a rule on top of the default
package without overriding it, using the same metadata-driven pattern as
the builtin refs-* rules."""
from __future__ import annotations

from typing import Any

from speclint.ir.types import SpecIR
from speclint.rules.registry import rule
from speclint.rules.types import ExpectedFinding, Finding, Fixture


_FIXTURES = [
    Fixture(
        name="owner-present-passes",
        files={"README.md": "# X\n"},
        metadata={"status": "accepted", "owner": "esben"},
        expects=(),
    ),
    Fixture(
        name="owner-missing-fires",
        files={"README.md": "# X\n"},
        metadata={"status": "accepted"},
        expects=(ExpectedFinding(file="contract.yaml", message_contains="owner"),),
    ),
    Fixture(
        name="no-metadata-short-circuits",
        files={"README.md": "# X\n"},
        # No metadata = the rule has nothing to enforce on, silently skips.
        expects=(),
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
    if not ir.metadata:
        return []
    field = config.get("field", "owner")
    if ir.metadata.get(field):
        return []
    sidecar_name = config.get("sidecar_filename", _guess_sidecar(ir))
    return [
        Finding(
            rule_id="frontmatter-owner",
            severity=config.get("severity", "warn"),
            file=sidecar_name,
            line=None,
            message=f"Missing required field `{field}`",
            hint=f"Add `{field}: <github-handle>` to the spec's metadata sidecar.",
        )
    ]


def _guess_sidecar(ir: SpecIR) -> str:
    try:
        for p in ir.folder.iterdir():
            if p.suffix in {".yml", ".yaml"} and p.is_file():
                return p.name
    except OSError:
        pass
    return "<metadata>"
