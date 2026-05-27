from __future__ import annotations

from io import StringIO

from ..runner import RunResult


def render_human(result: RunResult) -> str:
    buf = StringIO()

    if result.rules_skipped_llm:
        buf.write(
            f"[llm] no transport available; skipping {len(result.rules_skipped_llm)} rule(s): "
            f"{', '.join(result.rules_skipped_llm)}\n"
            f"      hint: set ANTHROPIC_API_KEY/OPENAI_API_KEY or install claude/codex CLI\n"
        )
    elif result.transport_chosen:
        buf.write(f"[llm] transport: {result.transport_chosen}\n")

    if result.rules_skipped_semantic:
        buf.write(
            f"[semantic] fastembed not installed; skipping "
            f"{len(result.rules_skipped_semantic)} rule(s): "
            f"{', '.join(result.rules_skipped_semantic)}\n"
            f"           hint: pip install 'speclint[semantic]'\n"
        )
    elif result.embedder_chosen:
        buf.write(f"[semantic] embedder: {result.embedder_chosen}\n")

    counts = {"error": 0, "warn": 0, "info": 0}
    for f in result.findings:
        if f.severity in counts:
            counts[f.severity] += 1

    by_spec: dict[str, list] = {}
    for f in result.findings:
        by_spec.setdefault(f.spec or "?", []).append(f)

    for spec in sorted(by_spec):
        spec_findings = by_spec[spec]
        buf.write(f"\n[spec] {spec}\n")
        for f in sorted(spec_findings, key=lambda x: (x.file or "", x.line or 0)):
            loc = f.file if f.line is None else f"{f.file}:{f.line}"
            fix_tag = "  [fixable]" if f.fix is not None else ""
            buf.write(f"  {f.severity:>5}  {f.rule_id:<24}  {loc}  {f.message}{fix_tag}\n")
            if f.hint:
                buf.write(f"          hint: {f.hint}\n")

    buf.write(
        f"\nsummary: {counts['error']} error(s), {counts['warn']} warn(s), {counts['info']} info "
        f"across {len(result.specs_checked)} spec(s), {result.rules_evaluated} rule(s) evaluated\n"
    )

    if result.override_log:
        buf.write("\noverride log:\n")
        for o in result.override_log:
            buf.write(f"  {o['rule_id']}: {o['from']} -> {o['to']}\n")

    return buf.getvalue()
