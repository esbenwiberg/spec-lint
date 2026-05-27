"""Markdown reporter — produces a PR-comment-ready report body.

Grouped by spec, severity badges, override audit, and a trailing
`<!-- speclint:report -->` marker so external tooling (GitHub Action,
bot) can detect and replace the prior comment instead of stacking new
ones."""
from __future__ import annotations

from io import StringIO

from ..rules.types import SEVERITY_RANK
from ..runner import RunResult

MARKER = "<!-- speclint:report -->"

_BADGE = {
    "error": "🛑 error",
    "warn": "⚠️ warn",
    "info": "ℹ️ info",
    "off": "off",
}


def render_markdown(result: RunResult) -> str:
    buf = StringIO()
    buf.write("## spec-lint report\n\n")

    counts = {"error": 0, "warn": 0, "info": 0}
    for f in result.findings:
        if f.severity in counts:
            counts[f.severity] += 1

    status_word = (
        "🛑 failing"
        if counts["error"]
        else ("⚠️ warnings" if counts["warn"] else "✅ clean")
    )
    buf.write(
        f"**{status_word}** — {counts['error']} error · "
        f"{counts['warn']} warn · {counts['info']} info "
        f"across {len(result.specs_checked)} spec(s), "
        f"{result.rules_evaluated} rule(s) evaluated.\n\n"
    )

    if result.rules_skipped_llm:
        buf.write(
            f"> _LLM transport unavailable — skipped: "
            f"`{', '.join(result.rules_skipped_llm)}`_\n\n"
        )

    if result.rules_skipped_semantic:
        buf.write(
            f"> _fastembed not installed — semantic rules skipped: "
            f"`{', '.join(result.rules_skipped_semantic)}`. "
            f"Install with `pip install 'speclint[semantic]'`._\n\n"
        )

    if not result.findings:
        buf.write("_No findings._\n\n")
        buf.write(MARKER + "\n")
        return buf.getvalue()

    by_spec: dict[str, list] = {}
    for f in result.findings:
        by_spec.setdefault(f.spec or "?", []).append(f)

    for spec in sorted(by_spec):
        spec_findings = sorted(
            by_spec[spec],
            key=lambda x: (-SEVERITY_RANK.get(x.severity, 0), x.file or "", x.line or 0),
        )
        buf.write(f"### `{spec}`\n\n")
        buf.write("| Severity | Rule | Location | Message |\n")
        buf.write("| --- | --- | --- | --- |\n")
        for f in spec_findings:
            loc = f.file or ""
            if f.line is not None:
                loc = f"{loc}:{f.line}"
            msg = _escape(f.message)
            if f.fix is not None:
                msg = f"🔧 {msg}"
            buf.write(
                f"| {_BADGE.get(f.severity, f.severity)} | `{f.rule_id}` | "
                f"`{_escape(loc)}` | {msg} |\n"
            )
        buf.write("\n")

    if result.override_log:
        buf.write("<details><summary>Rule overrides</summary>\n\n")
        for o in result.override_log:
            buf.write(f"- `{o['rule_id']}`: `{o['from']}` → `{o['to']}`\n")
        buf.write("\n</details>\n\n")

    buf.write(MARKER + "\n")
    return buf.getvalue()


def _escape(s: str) -> str:
    """Escape pipe/newline so cell content can't break the table."""
    return s.replace("|", "\\|").replace("\n", " ")
