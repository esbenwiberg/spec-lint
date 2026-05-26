"""GitHub workflow-commands reporter — emits `::warning::`/`::error::`
lines so each finding renders as an inline PR annotation.

See: https://docs.github.com/actions/learn-github-actions/workflow-commands-for-github-actions
"""
from __future__ import annotations

from io import StringIO

from ..runner import RunResult

_LEVEL = {"error": "error", "warn": "warning", "info": "notice"}


def render_github(result: RunResult) -> str:
    buf = StringIO()
    for f in result.findings:
        level = _LEVEL.get(f.severity)
        if level is None:
            continue
        params = []
        if f.spec and f.file:
            # Annotations need a path relative to repo root. The spec name is
            # the folder under specs/, and f.file is relative to the spec
            # folder. We can't reconstruct the full repo-relative path here
            # without knowing the specs glob — but `specs/<spec>/<file>` is
            # the convention and matches the default config.
            params.append(f"file=specs/{f.spec}/{f.file}")
        elif f.file:
            params.append(f"file={f.file}")
        if f.line is not None:
            params.append(f"line={f.line}")
        params.append(f"title=speclint/{f.rule_id}")
        msg = _escape(f.message)
        buf.write(f"::{level} {','.join(params)}::{msg}\n")
    return buf.getvalue()


def _escape(s: str) -> str:
    """Escape workflow-command special characters in the message body."""
    return (
        s.replace("%", "%25")
        .replace("\r", "%0D")
        .replace("\n", "%0A")
    )
