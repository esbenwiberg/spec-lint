"""Tier 3 rule: flag pairs of claims in a single spec that contradict
each other.

Static and semantic rules can find duplicates (claim-redundancy) and
syntactic noise (no-tbd, no-weasel-words). They can't tell when
*purpose.md* says "OAuth-only authentication" and *design.md* later
says "API keys remain supported." That's the kind of contradiction that
slips through review because each file reads fine in isolation.

This rule batches every markdown body in the spec into one LLM call
with line numbers, asks for *only* high-confidence contradictions
(not tradeoffs, not scope ambiguity), and emits one finding per
contradiction. Defaults to ``info`` — LLM verdicts on this are
necessarily judgment calls."""
from __future__ import annotations

import json
from typing import Any

from ...ir.types import SpecIR
from ..registry import rule
from ..types import ExpectedFinding, Finding, Fixture


_PROMPT_HEADER = (
    "You are reviewing a spec for internal contradictions. The spec is "
    "split across one or more files, each with line numbers. List ONLY "
    "clear, high-confidence contradictions where two statements cannot "
    "both be true at the same time. Do NOT flag scope decisions, "
    "tradeoffs, design ambiguity, evolving sections, or examples that "
    "intentionally violate a rule for illustration. Only flag concrete "
    "logical contradictions between claims about how the system works.\n\n"
    "Return ONLY a JSON object with one key, `contradictions`, whose "
    "value is an array of objects with keys: `file_a`, `line_a`, "
    "`claim_a` (<=120 chars), `file_b`, `line_b`, `claim_b` (<=120 "
    "chars), `why` (one sentence). If nothing contradicts, return "
    "{\"contradictions\": []}. No prose, no markdown, just JSON.\n\n"
)


_FIXTURES = [
    Fixture(
        name="cross-file-contradiction-fires",
        files={
            "purpose.md": "# Purpose\n\nThe API supports OAuth ONLY.\n",
            "design.md": "# Design\n\nAPI keys remain supported for legacy clients.\n",
        },
        options={
            "_llm_call": lambda prompt: json.dumps({
                "contradictions": [{
                    "file_a": "purpose.md",
                    "line_a": 3,
                    "claim_a": "The API supports OAuth ONLY",
                    "file_b": "design.md",
                    "line_b": 3,
                    "claim_b": "API keys remain supported for legacy clients",
                    "why": "OAuth-only and API-keys-supported cannot both hold",
                }],
            }),
        },
        expects=(
            ExpectedFinding(
                file="purpose.md",
                line=3,
                message_contains="design.md",
            ),
        ),
    ),
    Fixture(
        name="no-contradictions-no-findings",
        files={
            "brief.md": "# x\n\nMUST validate.\n",
            "design.md": "# y\n\nValidation uses regex.\n",
        },
        options={
            "_llm_call": lambda prompt: '{"contradictions": []}',
        },
        expects=(),
    ),
    Fixture(
        name="single-file-still-checks",
        files={
            "brief.md": (
                "# x\n\n"
                "The cache MUST be enabled in production.\n"
                "Caching is disabled in this release.\n"
            ),
        },
        options={
            "_llm_call": lambda prompt: json.dumps({
                "contradictions": [{
                    "file_a": "brief.md", "line_a": 3,
                    "claim_a": "cache MUST be enabled in production",
                    "file_b": "brief.md", "line_b": 4,
                    "claim_b": "Caching is disabled in this release",
                    "why": "MUST-enabled vs disabled cannot both hold",
                }],
            }),
        },
        expects=(
            ExpectedFinding(file="brief.md", line=3, message_contains="line 4"),
        ),
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
        name="invalid-line-numbers-dropped",
        files={"brief.md": "# x\n\nMUST do X.\n"},
        options={
            "_llm_call": lambda prompt: json.dumps({
                "contradictions": [{
                    "file_a": "brief.md", "line_a": 999,
                    "claim_a": "fake claim a",
                    "file_b": "brief.md", "line_b": 1000,
                    "claim_b": "fake claim b",
                    "why": "hallucinated",
                }],
            }),
        },
        expects=(),
    ),
    Fixture(
        name="unknown-file-dropped",
        files={"brief.md": "# x\n\nMUST do X.\n"},
        options={
            "_llm_call": lambda prompt: json.dumps({
                "contradictions": [{
                    "file_a": "nonexistent.md", "line_a": 1,
                    "claim_a": "a", "file_b": "brief.md", "line_b": 3,
                    "claim_b": "b", "why": "hallucinated file",
                }],
            }),
        },
        expects=(),
    ),
    Fixture(
        name="multiple-contradictions-fire-separately",
        files={
            "a.md": "# a\n\nClaim alpha.\nClaim beta.\n",
            "b.md": "# b\n\nClaim gamma.\nClaim delta.\n",
        },
        options={
            "_llm_call": lambda prompt: json.dumps({
                "contradictions": [
                    {"file_a": "a.md", "line_a": 3, "claim_a": "alpha",
                     "file_b": "b.md", "line_b": 3, "claim_b": "gamma",
                     "why": "first"},
                    {"file_a": "a.md", "line_a": 4, "claim_a": "beta",
                     "file_b": "b.md", "line_b": 4, "claim_b": "delta",
                     "why": "second"},
                ],
            }),
        },
        expects=(
            ExpectedFinding(file="a.md", line=3, message_contains="first"),
            ExpectedFinding(file="a.md", line=4, message_contains="second"),
        ),
    ),
]


@rule(
    id="internal-contradiction",
    version="1.0.0",
    tier="llm",
    default_severity="info",
    rationale=(
        "Two statements in the same spec that can't both be true is a "
        "high-cost defect — readers in different parts of the spec form "
        "different mental models. Static rules can't see across-file "
        "contradiction; this rule asks a model to. Off unless a "
        "transport is available."
    ),
    fixtures=_FIXTURES,
)
def check(ir: SpecIR, config: dict[str, Any]) -> list[Finding]:
    llm_call = config.get("_llm_call")
    if llm_call is None:
        return []
    if not ir.raw_text:
        return []

    max_lines_per_file = int(config.get("max_lines_per_file", 400))
    spec_blob = _build_numbered_spec(ir, max_lines_per_file)
    if not spec_blob.strip():
        return []

    prompt = _PROMPT_HEADER + spec_blob + "\n"
    try:
        raw = llm_call(prompt)
    except Exception:
        return []

    items = _parse_contradictions(raw)
    if not items:
        return []

    severity = config.get("severity", "info")
    findings: list[Finding] = []
    for c in items:
        file_a = c.get("file_a", "")
        file_b = c.get("file_b", "")
        line_a = _coerce_line(c.get("line_a"))
        line_b = _coerce_line(c.get("line_b"))
        if not (file_a and file_b and line_a and line_b):
            continue
        if file_a not in ir.raw_text or file_b not in ir.raw_text:
            continue
        if not _line_in_range(ir.raw_text[file_a], line_a):
            continue
        if not _line_in_range(ir.raw_text[file_b], line_b):
            continue
        claim_a = (c.get("claim_a") or "").strip()
        claim_b = (c.get("claim_b") or "").strip()
        why = (c.get("why") or "").strip()
        findings.append(
            Finding(
                rule_id="internal-contradiction",
                severity=severity,
                file=file_a,
                line=line_a,
                message=(
                    f"Contradicts {file_b}:line {line_b} — {why}"
                ),
                hint=(
                    f"This claim: {claim_a!r}. Conflicting claim: "
                    f"{claim_b!r}. Reconcile the two or scope the "
                    "conflict explicitly."
                ),
            )
        )
    return findings


def _build_numbered_spec(ir: SpecIR, max_lines_per_file: int) -> str:
    blocks: list[str] = []
    for rel, text in ir.raw_text.items():
        lines = text.splitlines()
        if len(lines) > max_lines_per_file:
            lines = lines[:max_lines_per_file] + [
                f"... [truncated, {len(text.splitlines()) - max_lines_per_file} more lines]"
            ]
        numbered = "\n".join(
            f"{i}: {line}" for i, line in enumerate(lines, start=1)
        )
        blocks.append(f"=== {rel} ===\n{numbered}")
    return "\n\n".join(blocks)


def _parse_contradictions(raw: str) -> list[dict[str, Any]]:
    cleaned = raw.strip()
    if cleaned.startswith("```"):
        cleaned = cleaned.strip("`")
        nl = cleaned.find("\n")
        if nl != -1:
            cleaned = cleaned[nl + 1 :]
    try:
        obj = json.loads(cleaned)
    except json.JSONDecodeError:
        return []
    if not isinstance(obj, dict):
        return []
    items = obj.get("contradictions")
    if not isinstance(items, list):
        return []
    return [c for c in items if isinstance(c, dict)]


def _coerce_line(v: Any) -> int | None:
    try:
        n = int(v)
    except (TypeError, ValueError):
        return None
    return n if n > 0 else None


def _line_in_range(text: str, line: int) -> bool:
    return 1 <= line <= max(1, len(text.splitlines()))
