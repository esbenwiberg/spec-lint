"""Override of the default `no-tbd` rule. This version flags ONLY 'XXX'
markers, not TBD/TODO/FIXME. Used to verify last-package-wins semantics."""
from __future__ import annotations

import re
from typing import Any

from speclint.ir.types import SpecIR
from speclint.rules.registry import rule
from speclint.rules.types import ExpectedFinding, Finding, Fixture


_FIXTURES = [
    Fixture(
        name="xxx-fires-but-tbd-does-not",
        files={
            "spec.yml": "id: x\nstatus: accepted\n",
            "README.md": "XXX hello TBD\n",
        },
        expects=(ExpectedFinding(message_contains="XXX"),),
    ),
]


@rule(
    id="no-tbd",  # SAME id as builtin — last package wins
    version="2.0.0",  # bumped so override audit shows different version
    tier="static",
    default_severity="error",  # team wants this loud
    rationale="Test plugin's stricter no-tbd; only fires on XXX markers.",
    fixtures=_FIXTURES,
)
def check(ir: SpecIR, config: dict[str, Any]) -> list[Finding]:
    if ir.manifest and ir.manifest.is_draft:
        return []
    regex = re.compile(r"\bXXX\b")
    findings: list[Finding] = []
    for file, text in ir.raw_text.items():
        for i, line in enumerate(text.splitlines(), start=1):
            if regex.search(line):
                findings.append(
                    Finding(
                        rule_id="no-tbd",
                        severity=config.get("severity", "error"),
                        file=file,
                        line=i,
                        message="Unresolved marker (plugin variant): XXX",
                    )
                )
    return findings
