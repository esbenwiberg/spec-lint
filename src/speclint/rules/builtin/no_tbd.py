from __future__ import annotations

import re
from typing import Any

from ...ir.types import SpecIR
from ..registry import rule
from ..types import ExpectedFinding, Finding, Fixture

_DEFAULT_PATTERNS = (r"TBD", r"TODO", r"FIXME", r"\?\?\?")


_FIXTURES = [
    Fixture(
        name="clean-accepted-spec",
        files={
            "spec.yml": "id: x\nstatus: accepted\n",
            "README.md": "# X\n\nThe API SHALL respond with 200 within 100ms.\n",
        },
        expects=(),  # zero findings expected
    ),
    Fixture(
        name="tbd-fires-on-accepted",
        files={
            "spec.yml": "id: x\nstatus: accepted\n",
            "README.md": "# X\n\nWe will figure caching out. TBD.\n",
        },
        expects=(ExpectedFinding(line=3, message_contains="TBD"),),
    ),
    Fixture(
        name="multiple-markers-fire-separately",
        files={
            "spec.yml": "id: x\nstatus: accepted\n",
            "README.md": "TBD\nTODO\nFIXME\n",
        },
        expects=(
            ExpectedFinding(line=1, message_contains="TBD"),
            ExpectedFinding(line=2, message_contains="TODO"),
            ExpectedFinding(line=3, message_contains="FIXME"),
        ),
    ),
    Fixture(
        name="draft-status-skips-rule",
        files={
            "spec.yml": "id: x\nstatus: draft\n",
            "README.md": "Lots of TBD and FIXME here, all allowed in draft.\n",
        },
        expects=(),  # draft spec → rule short-circuits
    ),
]


@rule(
    id="no-tbd",
    version="1.0.0",
    tier="static",
    default_severity="warn",
    rationale=(
        "Specs above status=draft should not contain unresolved markers. "
        "Move open questions into a dedicated file or resolve them."
    ),
    fixtures=_FIXTURES,
)
def check(ir: SpecIR, config: dict[str, Any]) -> list[Finding]:
    if ir.manifest and ir.manifest.is_draft:
        return []

    patterns = config.get("patterns", _DEFAULT_PATTERNS)
    regex = re.compile(r"\b(" + "|".join(patterns) + r")\b")
    severity = config.get("severity", "warn")

    findings: list[Finding] = []
    for file, text in ir.raw_text.items():
        for i, line in enumerate(text.splitlines(), start=1):
            m = regex.search(line)
            if m:
                findings.append(
                    Finding(
                        rule_id="no-tbd",
                        severity=severity,
                        file=file,
                        line=i,
                        message=f"Unresolved marker: {m.group(0)}",
                        hint="Resolve, move to open-questions.md, or set status: draft.",
                    )
                )
    return findings
