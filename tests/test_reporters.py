"""Reporter output contracts. Tests validate the *shape* of the rendered
text — what consumers (PR bots, GitHub annotations) actually parse — not
the exact prose, which is allowed to evolve."""
from __future__ import annotations

import json

from speclint.reporters import MARKER, render_github, render_human, render_json, render_markdown
from speclint.rules.types import Finding
from speclint.runner import RunResult


def _result_with(*findings: Finding, **kw) -> RunResult:
    r = RunResult(findings=list(findings))
    r.specs_checked = kw.get("specs_checked", ["demo"])
    r.rules_evaluated = kw.get("rules_evaluated", 3)
    r.override_log = kw.get("override_log", [])
    r.rules_skipped_llm = kw.get("rules_skipped_llm", [])
    r.transport_chosen = kw.get("transport_chosen")
    r.package_order = kw.get("package_order", ["default"])
    return r


_FINDINGS = [
    Finding(rule_id="no-tbd", severity="warn", file="README.md", line=12,
            message="Unresolved marker: TBD", spec="demo"),
    Finding(rule_id="refs-resolve", severity="error", file="spec.yml", line=None,
            message="`references` glob matches no files: src/x/**", spec="demo"),
]


# ---------- markdown ----------

def test_markdown_includes_sticky_marker():
    out = render_markdown(_result_with(*_FINDINGS))
    assert MARKER in out
    assert out.rstrip().endswith(MARKER)


def test_markdown_empty_result_renders_clean():
    out = render_markdown(_result_with())
    assert "✅" in out or "clean" in out
    assert "No findings" in out
    assert MARKER in out


def test_markdown_counts_by_severity():
    out = render_markdown(_result_with(*_FINDINGS))
    assert "1 error" in out
    assert "1 warn" in out


def test_markdown_escapes_pipe_in_message():
    f = Finding(rule_id="x", severity="warn", file="f.md", line=1,
                message="bad | char", spec="demo")
    out = render_markdown(_result_with(f))
    assert "bad \\| char" in out


def test_markdown_renders_override_log_when_present():
    out = render_markdown(_result_with(
        *_FINDINGS,
        override_log=[{"rule_id": "no-tbd", "from": "default", "to": "myteam"}],
    ))
    assert "Rule overrides" in out
    assert "default` → `myteam" in out


# ---------- github workflow commands ----------

def test_github_emits_warning_and_error_levels():
    out = render_github(_result_with(*_FINDINGS))
    lines = out.strip().splitlines()
    assert any(l.startswith("::warning ") for l in lines)
    assert any(l.startswith("::error ") for l in lines)


def test_github_prefixes_file_with_specs_dir():
    out = render_github(_result_with(*_FINDINGS))
    assert "file=specs/demo/README.md" in out
    assert "file=specs/demo/spec.yml" in out


def test_github_includes_rule_id_in_title():
    out = render_github(_result_with(*_FINDINGS))
    assert "title=speclint/no-tbd" in out
    assert "title=speclint/refs-resolve" in out


def test_github_escapes_newlines_and_percent():
    f = Finding(rule_id="x", severity="warn", file="f.md", line=1,
                message="line1\nline2 100%", spec="demo")
    out = render_github(_result_with(f))
    assert "%0A" in out
    assert "100%25" in out
    # Raw newline inside message body would break the workflow command
    body_after_separator = out.split("::", 2)[-1].split("\n", 1)[0]
    assert "\n" not in body_after_separator


def test_github_omits_line_when_none():
    out = render_github(_result_with(_FINDINGS[1]))  # line=None finding
    line = out.strip()
    assert "line=" not in line


# ---------- existing formats stay healthy ----------

def test_human_still_renders_basic_summary():
    out = render_human(_result_with(*_FINDINGS))
    assert "summary:" in out
    assert "1 error" in out
    assert "1 warn" in out


def test_json_is_valid_and_includes_findings():
    out = render_json(_result_with(*_FINDINGS))
    parsed = json.loads(out)
    assert len(parsed["findings"]) == 2
    assert {f["rule_id"] for f in parsed["findings"]} == {"no-tbd", "refs-resolve"}
