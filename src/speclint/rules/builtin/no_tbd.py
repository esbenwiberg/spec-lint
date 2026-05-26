from __future__ import annotations

import re
from typing import Any

from ...ir.types import SpecIR
from ..registry import rule
from ..types import ExpectedFinding, Finding, Fixture

_DEFAULT_PATTERNS = (r"TBD", r"TODO", r"FIXME", r"\?\?\?")


_FIXTURES = [
    Fixture(
        name="clean-spec-no-markers",
        files={"README.md": "# X\n\nThe API SHALL respond with 200 within 100ms.\n"},
        expects=(),
    ),
    Fixture(
        name="tbd-fires",
        files={"README.md": "# X\n\nWe will figure caching out. TBD.\n"},
        expects=(ExpectedFinding(line=3, message_contains="TBD"),),
    ),
    Fixture(
        name="multiple-markers-fire-separately",
        files={"README.md": "TBD\nTODO\nFIXME\n"},
        expects=(
            ExpectedFinding(line=1, message_contains="TBD"),
            ExpectedFinding(line=2, message_contains="TODO"),
            ExpectedFinding(line=3, message_contains="FIXME"),
        ),
    ),
    Fixture(
        name="lowercase-markers-also-fire",
        files={"README.md": "tbd in a sentence\nfixme: this too\n"},
        expects=(
            ExpectedFinding(line=1, message_contains="tbd"),
            ExpectedFinding(line=2, message_contains="fixme"),
        ),
    ),
    Fixture(
        name="draft-status-skips-rule",
        files={"README.md": "Lots of TBD and FIXME here, all allowed in draft.\n"},
        metadata={"status": "draft"},
        expects=(),
    ),
    Fixture(
        name="non-draft-status-still-fires",
        files={"README.md": "TBD in an accepted spec\n"},
        metadata={"status": "accepted"},
        expects=(ExpectedFinding(line=1, message_contains="TBD"),),
    ),
]


@rule(
    id="no-tbd",
    version="1.0.0",
    tier="static",
    default_severity="warn",
    rationale=(
        "Specs that aren't drafts should not contain unresolved markers. "
        "Move open questions into a dedicated file or resolve them. "
        "Skipped when the spec's metadata declares status=draft (key is "
        "configurable via the `status_field` option)."
    ),
    fixtures=_FIXTURES,
)
def check(ir: SpecIR, config: dict[str, Any]) -> list[Finding]:
    status_field = config.get("status_field", "status")
    if ir.metadata.get(status_field) == "draft":
        return []

    patterns = config.get("patterns", _DEFAULT_PATTERNS)
    regex = re.compile(r"\b(" + "|".join(patterns) + r")\b", re.IGNORECASE)
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
