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

from ...ir.types import Claim, Heading, SpecIR
from ..registry import rule
from ..types import ExpectedFinding, Finding, Fixture


# Section headings that are narrative by convention. Claims that fall
# under one of these are skipped — they describe *why* the spec exists,
# not what the system must do. Match is case-insensitive and matches the
# heading text exactly (after stripping). Sub-sections inherit until a
# new heading at the same or shallower level appears.
_NARRATIVE_SECTIONS = frozenset({
    "problem",
    "background",
    "context",
    "motivation",
    "rationale",
    "why",
    "users",
    "out of scope",
    "non-goals",
    "non goals",
    "glossary",
    "definitions",
    "terminology",
})


_PROMPT_HEADER = (
    "You are a strict QA reviewer. For each numbered claim below, "
    "decide whether it is mechanically verifiable — could a "
    "deterministic test, command, or measurement prove it true or "
    "false?\n\n"
    "TESTABLE = names a concrete observable. This INCLUDES:\n"
    "  - state transitions (\"switches to X mode\", \"flag flips to true\")\n"
    "  - UI elements rendering or disappearing (\"shows Verification "
    "chapter\", \"hides the upgrade banner\")\n"
    "  - persistence writes (\"records `verified` in finding_dismissals\")\n"
    "  - API response shapes, status codes, thresholds, metrics\n"
    "  - function calls, errors raised, files written\n"
    "  - conditional behavior with concrete triggers (\"when zero open "
    "findings AND ≥1 sticky trigger, the wizard renders the Verification "
    "chapter\")\n\n"
    "VAGUE = aspirational, subjective, or unmeasurable. Examples: "
    "\"errors are handled gracefully\", \"the UI feels modern\", "
    "\"supports high concurrency\", \"is robust\", \"is performant\", "
    "\"users feel confident\".\n\n"
    "Be CONSERVATIVE — when in doubt, prefer TESTABLE. A behavior "
    "description with a concrete trigger and a concrete result is "
    "TESTABLE even if the language is plain English. Only return VAGUE "
    "when the claim hinges on a subjective quality (feel/robust/elegant) "
    "or an unbounded property (high/fast/scalable) with no threshold.\n\n"
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
        name="problem-section-skipped",
        files={
            "brief.md": (
                "# x\n\n"
                "## Problem\n\n"
                "When users hit the dashboard, the system MUST be helpful.\n"
                "\n"
                "## Requirements\n\n"
                "The API MUST return 401 on missing auth.\n"
            ),
        },
        options={
            "_llm_call": lambda prompt: '{"results": ["TESTABLE"]}',
        },
        expects=(),
    ),
    Fixture(
        name="narrative-sections-skipped-but-acceptance-graded",
        files={
            "brief.md": (
                "# x\n\n"
                "## Background\n\n"
                "Given a logged-in user, when they click, then it MUST work.\n"
                "\n"
                "## Rationale\n\n"
                "The system MUST be elegant.\n"
                "\n"
                "## Acceptance\n\n"
                "The API MUST be performant.\n"
            ),
        },
        options={
            # Only the Acceptance claim should reach the LLM. If the
            # filter is broken and all three are sent, this fixture
            # would still pass length-1 only if the LLM call ignored
            # the others — but the rule slices to candidates only, so
            # this is a structural check on the filter.
            "_llm_call": lambda prompt: '{"results": ["VAGUE"]}',
        },
        expects=(
            ExpectedFinding(line=13, message_contains="performant"),
        ),
    ),
    Fixture(
        name="all-claims-narrative-no-llm-call",
        files={
            "brief.md": (
                "# x\n\n"
                "## Problem\n\n"
                "Currently the wizard MUST be opened by the reviewer.\n"
                "When a developer fixes findings, the wizard goes stale.\n"
            ),
        },
        options={
            "_llm_call": lambda prompt: (_ for _ in ()).throw(
                AssertionError("rule should not call LLM when all claims are narrative")
            ),
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

    candidates = [c for c in ir.claims if not _in_narrative_section(c, ir.headings)]
    if not candidates:
        return []

    max_claims = int(config.get("max_claims", 50))
    claims = candidates[:max_claims]
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


def _in_narrative_section(claim: Claim, headings: list[Heading]) -> bool:
    """True if the claim falls under a heading whose text is in the
    narrative allowlist. Walks the closest ancestor headings (same file,
    line < claim.line) and inherits from the deepest one. The check only
    fires when the immediately-enclosing heading is narrative; switching
    into a deeper sub-section (e.g. `### Acceptance` under `## Problem`)
    breaks the inheritance and the claim is graded normally."""
    in_file = [h for h in headings if h.file == claim.file and h.line < claim.line]
    if not in_file:
        return False
    in_file.sort(key=lambda h: h.line)
    stack: list[Heading] = []
    for h in in_file:
        while stack and stack[-1].level >= h.level:
            stack.pop()
        stack.append(h)
    if not stack:
        return False
    return stack[-1].text.strip().lower() in _NARRATIVE_SECTIONS


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
