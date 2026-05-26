from __future__ import annotations

import re
from typing import Any

from ...ir.types import SpecIR
from ..registry import rule
from ..types import ExpectedFinding, Finding, Fixture

_DEFAULT_WORDS = (
    "fast",
    "scalable",
    "robust",
    "user-friendly",
    "simply",
    "just",
    "easy",
    "intuitive",
    "performant",
    "modern",
)


_FIXTURES = [
    Fixture(
        name="concrete-language-passes",
        files={
            "spec.yml": "id: x\nstatus: accepted\n",
            "README.md": (
                "# X\n\n"
                "The API SHALL respond within 200ms p95. Requests exceeding 10MB "
                "are rejected with HTTP 413.\n"
            ),
        },
        expects=(),
    ),
    Fixture(
        name="weasel-words-fire-multiple",
        files={
            "spec.yml": "id: x\nstatus: accepted\n",
            "README.md": "# X\n\nThe API is fast, scalable, and robust.\n",
        },
        expects=(
            ExpectedFinding(line=3, message_contains="fast"),
            ExpectedFinding(line=3, message_contains="scalable"),
            ExpectedFinding(line=3, message_contains="robust"),
        ),
    ),
    Fixture(
        name="case-insensitive-match",
        files={
            "spec.yml": "id: x\nstatus: accepted\n",
            "README.md": "# X\n\nThis is FAST and Robust.\n",
        },
        expects=(
            ExpectedFinding(message_contains="FAST"),
            ExpectedFinding(message_contains="Robust"),
        ),
    ),
]


@rule(
    id="no-weasel-words",
    version="1.0.0",
    tier="static",
    default_severity="warn",
    rationale=(
        "Subjective adjectives like 'fast' or 'scalable' are not testable. "
        "Replace with concrete thresholds or metrics."
    ),
    fixtures=_FIXTURES,
)
def check(ir: SpecIR, config: dict[str, Any]) -> list[Finding]:
    words = config.get("words", _DEFAULT_WORDS)
    severity = config.get("severity", "warn")
    regex = re.compile(r"\b(" + "|".join(re.escape(w) for w in words) + r")\b", re.IGNORECASE)

    findings: list[Finding] = []
    for file, text in ir.raw_text.items():
        for i, line in enumerate(text.splitlines(), start=1):
            for m in regex.finditer(line):
                findings.append(
                    Finding(
                        rule_id="no-weasel-words",
                        severity=severity,
                        file=file,
                        line=i,
                        message=f"Weasel word: '{m.group(0)}'",
                        hint="Replace with a measurable threshold or metric.",
                    )
                )
    return findings
