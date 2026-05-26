"""Tier 3 second-pass classifier for low-signal weasel words.

The Tier 1 ``no-weasel-words`` rule emits ``info`` findings for words
with a real false-positive rate (``fast`` in "Fast Refresh", ``just`` in
"the contract is just the YAML"). This rule asks a small model whether
each candidate is actually an unmeasurable claim, and *promotes* the
true positives to ``warn``.

Design choices:
- Reuses the regex/scan from the static rule rather than re-running it
  with different word lists. Same words, smarter verdict.
- One LLM call per spec — batches every candidate line into a single
  prompt to keep cost predictable (a typical spec runs 0–1 calls).
- Outputs *new* findings rather than mutating the Tier 1 ones. The
  reporter already deduplicates by (rule_id, file, line, message), so
  the info-tier finding and the warn-tier promotion coexist without
  noise; users can disable the static rule if they want a single signal.
- Graceful skip when the runner didn't bind ``_llm_call`` (no transport
  available) — the runner already drops this rule in that case, but the
  defensive check keeps fixture tests trivial."""
from __future__ import annotations

import json
import re
from typing import Any

from ...ir.types import SpecIR
from ..registry import rule
from ..types import ExpectedFinding, Finding, Fixture
from .no_weasel_words import _DEFAULT_INFO_WORDS


_PROMPT_HEADER = (
    "You are a strict technical writing reviewer. For each numbered line "
    "below, decide whether the highlighted word is being used as an "
    "unmeasurable subjective claim about a system's behavior (CLAIM) or "
    "as benign context — a code identifier, a product name, a literal "
    "quotation, a hedge in casual prose, or a non-claim adverbial usage "
    "(BENIGN).\n\n"
    "Return ONLY a JSON object with one key, `results`, whose value is "
    "an array of strings `CLAIM` or `BENIGN` in the same order as the "
    "input. No prose, no markdown, just JSON.\n\n"
    "Examples:\n"
    "  'The API is FAST' (word: fast) → CLAIM\n"
    "  'Fast Refresh hot-reloads modules' (word: Fast) → BENIGN\n"
    "  'you can JUST retry the request' (word: just) → CLAIM\n"
    "  'the contract is JUST the YAML file' (word: just) → BENIGN\n\n"
    "Lines:\n"
)


_FIXTURES = [
    Fixture(
        name="promotes-claim-uses-to-warn",
        files={
            "README.md": (
                "# X\n\n"
                "The API is fast.\n"
                "Fast Refresh hot-reloads modules.\n"
            ),
        },
        options={
            "_llm_call": lambda prompt: '{"results": ["CLAIM", "BENIGN"]}',
        },
        expects=(
            ExpectedFinding(line=3, severity="warn", message_contains="fast"),
        ),
    ),
    Fixture(
        name="no-candidates-no-call",
        files={
            "README.md": (
                "# X\n\nThe API SHALL respond within 200ms p95.\n"
            ),
        },
        # Caller would explode if invoked; the rule must short-circuit.
        options={
            "_llm_call": lambda prompt: (_ for _ in ()).throw(
                AssertionError("rule should not call LLM with no candidates")
            ),
        },
        expects=(),
    ),
    Fixture(
        name="all-benign-no-promotions",
        files={
            "README.md": (
                "# X\n\nFast Refresh is just an example.\n"
            ),
        },
        options={
            "_llm_call": lambda prompt: '{"results": ["BENIGN", "BENIGN"]}',
        },
        expects=(),
    ),
    Fixture(
        name="malformed-response-graceful-no-promotions",
        files={
            "README.md": "# X\n\nThe API is fast.\n",
        },
        options={"_llm_call": lambda prompt: "not json"},
        expects=(),
    ),
    Fixture(
        name="missing-llm-caller-graceful-skip",
        files={"README.md": "# X\n\nThe API is fast.\n"},
        # No _llm_call in options — simulates runner dropping the rule.
        expects=(),
    ),
]


@rule(
    id="no-weasel-words-llm",
    version="1.0.0",
    tier="llm",
    default_severity="warn",
    rationale=(
        "Promotes low-signal weasel hits (fast/just/simply/easy/modern) "
        "to `warn` when a small model confirms they are unmeasurable "
        "claims rather than benign context. Off unless a transport is "
        "available."
    ),
    fixtures=_FIXTURES,
)
def check(ir: SpecIR, config: dict[str, Any]) -> list[Finding]:
    llm_call = config.get("_llm_call")
    if llm_call is None:
        return []

    words = config.get("info_words", _DEFAULT_INFO_WORDS)
    if not words:
        return []
    regex = re.compile(
        r"\b(" + "|".join(re.escape(w) for w in words) + r")\b",
        re.IGNORECASE,
    )

    candidates: list[tuple[str, int, str, str]] = []  # (file, line, word, snippet)
    for file, text in ir.raw_text.items():
        for i, line in enumerate(text.splitlines(), start=1):
            for m in regex.finditer(line):
                candidates.append((file, i, m.group(0), line.strip()))
    if not candidates:
        return []

    prompt = _build_prompt(candidates)
    try:
        raw = llm_call(prompt)
    except Exception:
        return []
    verdicts = _parse_verdicts(raw, len(candidates))
    if verdicts is None:
        return []

    severity = config.get("severity", "warn")
    findings: list[Finding] = []
    for (file, line, word, _snippet), verdict in zip(candidates, verdicts):
        if verdict != "CLAIM":
            continue
        findings.append(
            Finding(
                rule_id="no-weasel-words-llm",
                severity=severity,
                file=file,
                line=line,
                message=f"Unmeasurable claim: '{word}'",
                hint=(
                    "A reviewer confirmed this reads as an unmeasurable "
                    "subjective claim. Replace with a threshold or metric."
                ),
            )
        )
    return findings


def _build_prompt(candidates: list[tuple[str, int, str, str]]) -> str:
    lines = []
    for n, (_file, _line, word, snippet) in enumerate(candidates, start=1):
        # Highlight the word in the snippet by uppercasing it (case-insensitive)
        # so the model sees which one we mean even when it appears multiple times.
        highlighted = re.sub(
            re.escape(word), word.upper(), snippet, count=1, flags=re.IGNORECASE
        )
        lines.append(f"  {n}. '{highlighted}' (word: {word})")
    return _PROMPT_HEADER + "\n".join(lines) + "\n"


def _parse_verdicts(raw: str, expected_n: int) -> list[str] | None:
    """Pull a ``results`` array out of the model's response. Returns
    None if the shape is wrong — caller will skip promotions, which is
    the safe default."""
    # Models occasionally wrap JSON in ``` fences despite instructions.
    cleaned = raw.strip()
    if cleaned.startswith("```"):
        cleaned = cleaned.strip("`")
        # Drop optional ``json`` language tag
        nl = cleaned.find("\n")
        if nl != -1:
            cleaned = cleaned[nl + 1 :]
    try:
        obj = json.loads(cleaned)
    except json.JSONDecodeError:
        return None
    results = obj.get("results") if isinstance(obj, dict) else None
    if not isinstance(results, list) or len(results) != expected_n:
        return None
    return [str(v).upper().strip() for v in results]
