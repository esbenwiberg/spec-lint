from __future__ import annotations

import re
from typing import Any

from ...ir.types import SpecIR
from ..registry import rule
from ..types import ExpectedFinding, Finding, Fixture

# High-signal weasels: almost always pure marketing copy in a spec.
_DEFAULT_WARN_WORDS = (
    "scalable",
    "robust",
    "user-friendly",
    "intuitive",
    "performant",
)

# Low-signal weasels: real false-positive rate (e.g. "Fast Refresh",
# "the contract is just the YAML", "easy mode"). Flagged at `info` so
# they surface without gating the build.
_DEFAULT_INFO_WORDS = (
    "fast",
    "just",
    "simply",
    "easy",
    "modern",
)


_FIXTURES = [
    Fixture(
        name="concrete-language-passes",
        files={
            "README.md":(
                "# X\n\n"
                "The API SHALL respond within 200ms p95. Requests exceeding 10MB "
                "are rejected with HTTP 413.\n"
            ),
        },
        expects=(),
    ),
    Fixture(
        name="warn-tier-words-fire-as-warn",
        files={
            "README.md":"# X\n\nThe API is scalable and robust.\n",
        },
        expects=(
            ExpectedFinding(line=3, severity="warn", message_contains="scalable"),
            ExpectedFinding(line=3, severity="warn", message_contains="robust"),
        ),
    ),
    Fixture(
        name="info-tier-words-fire-as-info",
        files={
            "README.md":"# X\n\nThe API is fast and easy to just use.\n",
        },
        expects=(
            ExpectedFinding(line=3, severity="info", message_contains="fast"),
            ExpectedFinding(line=3, severity="info", message_contains="easy"),
            ExpectedFinding(line=3, severity="info", message_contains="just"),
        ),
    ),
    Fixture(
        name="case-insensitive-match",
        files={
            "README.md":"# X\n\nThis is FAST and Robust.\n",
        },
        expects=(
            ExpectedFinding(severity="info", message_contains="FAST"),
            ExpectedFinding(severity="warn", message_contains="Robust"),
        ),
    ),
]


@rule(
    id="no-weasel-words",
    version="2.0.0",
    tier="static",
    default_severity="warn",
    rationale=(
        "Subjective adjectives like 'scalable' or 'robust' are not testable. "
        "Replace with concrete thresholds or metrics. Lower-signal words "
        "(fast/just/simply/easy/modern) fire at `info` because they have a "
        "real false-positive rate in code-adjacent prose."
    ),
    fixtures=_FIXTURES,
)
def check(ir: SpecIR, config: dict[str, Any]) -> list[Finding]:
    warn_words = config.get("words", _DEFAULT_WARN_WORDS)
    info_words = config.get("info_words", _DEFAULT_INFO_WORDS)
    # A user-set `severity` applies only to the warn tier; info-tier words
    # stay at info unless explicitly overridden via `info_severity`.
    warn_severity = config.get("severity", "warn")
    info_severity = config.get("info_severity", "info")

    tiers: list[tuple[tuple[str, ...], str]] = []
    if warn_words:
        tiers.append((tuple(warn_words), warn_severity))
    if info_words:
        tiers.append((tuple(info_words), info_severity))

    findings: list[Finding] = []
    for words, severity in tiers:
        if not words:
            continue
        regex = re.compile(
            r"\b(" + "|".join(re.escape(w) for w in words) + r")\b",
            re.IGNORECASE,
        )
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
