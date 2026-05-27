"""Tier 3 rule: grade each declarative claim in a spec for mechanical
testability.

`no-weasel-words` catches *lexical* vagueness (the word "fast"); this
rule catches *structural* vagueness — claims that are syntactically
well-formed but can't be deterministically verified. Examples that
slip past static rules:

- "Errors should be handled gracefully."
- "The UI should feel modern."
- "The system MUST support high concurrency."

All read as legitimate MUST/SHOULD statements. None can be turned into
a passing or failing test without further interpretation.

The rule pulls every claim the IR already extracted
(MUST/SHALL/SHOULD/MAY plus Gherkin Given/When/Then), batches them
into one LLM call, asks for a binary verdict per claim, and emits a
finding per VAGUE verdict at ``warn``. Default severity is ``warn``
because vague claims are how specs get signed off without anyone being
able to say what success looks like."""
from __future__ import annotations

import json
from typing import Any

from ...ir.types import SpecIR
from ..registry import rule
from ..types import ExpectedFinding, Finding, Fixture


_PROMPT_HEADER = (
    "You are a strict QA reviewer. For each numbered claim below, "
    "decide whether it is mechanically verifiable — could a "
    "deterministic test, command, or measurement prove it true or "
    "false?\n\n"
    "TESTABLE = names a concrete observable: an API response shape, a "
    "threshold or metric, a file/state change, a specific UI element, "
    "a function being called, an error being raised, etc.\n"
    "VAGUE = aspirational, subjective, or unmeasurable. Examples: "
    "\"errors are handled gracefully\", \"the UI feels modern\", "
    "\"supports high concurrency\", \"is robust\", \"is performant\".\n\n"
    "Be conservative — when in doubt, prefer TESTABLE. Gherkin "
    "Given/When/Then steps with concrete actions are TESTABLE. "
    "MUST/SHALL claims with a measurable noun are TESTABLE. Only flag "
    "VAGUE when the claim is genuinely unmeasurable.\n\n"
    "Return ONLY a JSON object with one key, `results`, whose value is "
    "an array of strings TESTABLE or VAGUE in the same order as input. "
    "No prose, no markdown, just JSON.\n\n"
    "Claims:\n"
)


_FIXTURES = [
    Fixture(
        name="flags-vague-claim",
        files={
            "brief.md": (
                "# x\n\n"
                "The system MUST be robust under load.\n"
                "The API MUST respond within 200ms p95.\n"
            ),
        },
        options={
            "_llm_call": lambda prompt: '{"results": ["VAGUE", "TESTABLE"]}',
        },
        expects=(
            ExpectedFinding(line=3, severity="warn", message_contains="robust"),
        ),
    ),
    Fixture(
        name="all-testable-no-findings",
        files={
            "brief.md": (
                "# x\n\n"
                "The API MUST return 401 on missing auth.\n"
                "Given a logged-in user, when they click logout, then the session is destroyed.\n"
            ),
        },
        options={
            "_llm_call": lambda prompt: '{"results": ["TESTABLE", "TESTABLE"]}',
        },
        expects=(),
    ),
    Fixture(
        name="no-claims-no-call",
        files={"brief.md": "# x\n\nJust prose, no claims here.\n"},
        options={
            "_llm_call": lambda prompt: (_ for _ in ()).throw(
                AssertionError("rule should not call LLM when there are no claims")
            ),
        },
        expects=(),
    ),
    Fixture(
        name="no-llm-transport-graceful-skip",
        files={"brief.md": "# x\n\nMUST do X.\n"},
        expects=(),
    ),
    Fixture(
        name="malformed-response-graceful",
        files={"brief.md": "# x\n\nMUST do X.\n"},
        options={"_llm_call": lambda prompt: "not json"},
        expects=(),
    ),
    Fixture(
        name="mismatched-results-length-graceful",
        files={
            "brief.md": "# x\n\nMUST do X.\nMUST do Y.\n",
        },
        options={
            "_llm_call": lambda prompt: '{"results": ["VAGUE"]}',
        },
        expects=(),
    ),
    Fixture(
        name="multiple-vague-fire-separately",
        files={
            "brief.md": (
                "# x\n\n"
                "The system MUST be performant.\n"
                "The UI MUST feel intuitive.\n"
                "The API MUST return JSON.\n"
            ),
        },
        options={
            "_llm_call": lambda prompt: (
                '{"results": ["VAGUE", "VAGUE", "TESTABLE"]}'
            ),
        },
        expects=(
            ExpectedFinding(line=3, message_contains="performant"),
            ExpectedFinding(line=4, message_contains="intuitive"),
        ),
    ),
]


@rule(
    id="testability-of-claims",
    version="1.0.0",
    tier="llm",
    default_severity="warn",
    rationale=(
        "A claim that can't be tested can't be enforced. Specs that "
        "ship with vague structural claims get signed off, then "
        "rediscovered as defects when the implementer asks what "
        "success looks like. Catches structural vagueness that "
        "`no-weasel-words` misses because the lexicon isn't the "
        "problem — the framing is."
    ),
    fixtures=_FIXTURES,
)
def check(ir: SpecIR, config: dict[str, Any]) -> list[Finding]:
    llm_call = config.get("_llm_call")
    if llm_call is None:
        return []
    if not ir.claims:
        return []

    max_claims = int(config.get("max_claims", 50))
    claims = list(ir.claims)[:max_claims]
    prompt = _build_prompt(claims)
    try:
        raw = llm_call(prompt)
    except Exception:
        return []

    verdicts = _parse_verdicts(raw, len(claims))
    if verdicts is None:
        return []

    severity = config.get("severity", "warn")
    findings: list[Finding] = []
    for claim, verdict in zip(claims, verdicts):
        if verdict != "VAGUE":
            continue
        excerpt = claim.text.strip()
        if len(excerpt) > 100:
            excerpt = excerpt[:97] + "..."
        findings.append(
            Finding(
                rule_id="testability-of-claims",
                severity=severity,
                file=claim.file,
                line=claim.line,
                message=f"Claim is not mechanically verifiable: {excerpt}",
                hint=(
                    "Rewrite with a concrete observable: a threshold, "
                    "an API response shape, a state change, a "
                    "specific UI element, or a metric. If it's "
                    "genuinely a non-functional desire, move it to a "
                    "rationale section."
                ),
            )
        )
    return findings


def _build_prompt(claims: list[Any]) -> str:
    lines: list[str] = []
    for i, c in enumerate(claims, start=1):
        snippet = c.text.strip()
        if len(snippet) > 200:
            snippet = snippet[:197] + "..."
        lines.append(f"  {i}. {snippet}")
    return _PROMPT_HEADER + "\n".join(lines) + "\n"


def _parse_verdicts(raw: str, expected_n: int) -> list[str] | None:
    cleaned = raw.strip()
    if cleaned.startswith("```"):
        cleaned = cleaned.strip("`")
        nl = cleaned.find("\n")
        if nl != -1:
            cleaned = cleaned[nl + 1 :]
    try:
        obj = json.loads(cleaned)
    except json.JSONDecodeError:
        return None
    if not isinstance(obj, dict):
        return None
    results = obj.get("results")
    if not isinstance(results, list) or len(results) != expected_n:
        return None
    return [str(v).upper().strip() for v in results]
