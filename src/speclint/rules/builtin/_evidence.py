"""Shared helper for Tier 3 rules: anchor a finding to a source line by
locating a verbatim evidence quote inside the text the model saw.

Why: LLMs are unreliable at counting lines but reliable at quoting
strings. Asking for `evidence` (a short substring of the original) and
resolving it server-side gives ESLint-grade precision without trusting
the model with arithmetic.
"""
from __future__ import annotations


def _normalize(s: str) -> str:
    return " ".join(s.split())


def find_line(text: str, quote: str) -> int | None:
    """Return the 1-based line of the first line containing `quote`.

    Match is whitespace-normalized (collapses runs of whitespace, strips
    leading/trailing) so the model doesn't need to reproduce indentation
    or trailing whitespace exactly. Returns ``None`` if `quote` is empty
    or not found — caller decides whether to drop the finding or fall
    back to a sidecar anchor.
    """
    needle = _normalize(quote)
    if not needle:
        return None
    for i, line in enumerate(text.splitlines(), start=1):
        if needle in _normalize(line):
            return i
    return None
