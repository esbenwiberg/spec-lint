"""Path B coupling rule: if a changed file matches this spec's `references`
globs and nothing inside the spec folder was changed in the same diff, the
spec is drifting away from the code it covers. Warn.

Requires diff context (SpecIR.changed_paths). Short-circuits silently when
no diff was provided (e.g., running speclint locally without --base)."""
from __future__ import annotations

import re
from typing import Any

from ...ir.types import SpecIR
from ..registry import rule
from ..types import ExpectedFinding, Finding, Fixture


_FIXTURES = [
    Fixture(
        name="code-touched-spec-also-touched-passes",
        files={
            "spec.yml": "id: x\nstatus: accepted\nreferences:\n  - 'src/x/**'\n",
            "README.md": "# x\n",
        },
        repo_files={"src/x/a.py": "x = 1\n"},
        changed_paths=("src/x/a.py", "spec/README.md"),
        expects=(),
    ),
    Fixture(
        name="code-touched-spec-not-touched-fires",
        files={
            "spec.yml": "id: x\nstatus: accepted\nreferences:\n  - 'src/x/**'\n",
            "README.md": "# x\n",
        },
        repo_files={"src/x/a.py": "x = 1\n"},
        changed_paths=("src/x/a.py",),
        expects=(
            ExpectedFinding(file="spec.yml", message_contains="src/x/a.py"),
        ),
    ),
    Fixture(
        name="unrelated-changes-do-not-fire",
        files={
            "spec.yml": "id: x\nstatus: accepted\nreferences:\n  - 'src/x/**'\n",
            "README.md": "# x\n",
        },
        repo_files={"src/y/b.py": "y = 2\n"},
        changed_paths=("src/y/b.py",),
        expects=(),
    ),
    Fixture(
        name="draft-spec-skips",
        files={
            "spec.yml": "id: x\nstatus: draft\nreferences:\n  - 'src/x/**'\n",
            "README.md": "# x\n",
        },
        changed_paths=("src/x/a.py",),
        expects=(),
    ),
    Fixture(
        name="no-diff-context-short-circuits",
        files={
            "spec.yml": "id: x\nstatus: accepted\nreferences:\n  - 'src/x/**'\n",
            "README.md": "# x\n",
        },
        changed_paths=None,
        expects=(),
    ),
    Fixture(
        name="empty-diff-no-findings",
        files={
            "spec.yml": "id: x\nstatus: accepted\nreferences:\n  - 'src/x/**'\n",
            "README.md": "# x\n",
        },
        changed_paths=(),
        expects=(),
    ),
    Fixture(
        name="multiple-matched-files-collapse-to-one-finding",
        files={
            "spec.yml": "id: x\nstatus: accepted\nreferences:\n  - 'src/x/**'\n",
            "README.md": "# x\n",
        },
        changed_paths=("src/x/a.py", "src/x/b.py", "src/x/sub/c.py"),
        expects=(
            ExpectedFinding(file="spec.yml", message_contains="3 file(s)"),
        ),
    ),
]


@rule(
    id="refs-coupling",
    version="1.0.0",
    tier="static",
    default_severity="warn",
    rationale=(
        "If you change code that a spec claims to cover, the spec should be "
        "reviewed in the same change. Otherwise specs silently drift away "
        "from the code they're supposed to describe."
    ),
    fixtures=_FIXTURES,
)
def check(ir: SpecIR, config: dict[str, Any]) -> list[Finding]:
    if ir.changed_paths is None:
        return []
    if ir.manifest is None or not ir.manifest.references:
        return []
    if ir.manifest.is_draft:
        return []

    spec_rel = ir.folder_relpath
    if spec_rel is None:
        return []

    spec_prefix = spec_rel.rstrip("/") + "/"
    spec_touched = any(
        p == spec_rel or p.startswith(spec_prefix) for p in ir.changed_paths
    )
    if spec_touched:
        return []

    matched = sorted(
        p for p in ir.changed_paths
        if any(_glob_match(g, p) for g in ir.manifest.references)
    )
    if not matched:
        return []

    severity = config.get("severity", "warn")
    preview = ", ".join(matched[:3])
    more = "" if len(matched) <= 3 else f" (+{len(matched) - 3} more)"
    return [
        Finding(
            rule_id="refs-coupling",
            severity=severity,
            file="spec.yml",
            line=None,
            message=(
                f"{len(matched)} file(s) under this spec's `references` "
                f"changed but the spec was not updated: {preview}{more}"
            ),
            hint=(
                "Update the spec in the same change, or mark it "
                "`status: deprecated` if it no longer applies."
            ),
        )
    ]


def _glob_match(pattern: str, path: str) -> bool:
    """Match `path` (posix, repo-root-relative) against a glob supporting
    `**`, `*`, `?`. `**` matches across `/` boundaries; `*` and `?` do not.
    """
    pattern = pattern.lstrip("/")
    parts: list[str] = []
    i = 0
    n = len(pattern)
    while i < n:
        if pattern[i:i + 3] == "**/":
            parts.append("(?:.*/)?")
            i += 3
        elif pattern[i:i + 2] == "**":
            parts.append(".*")
            i += 2
        elif pattern[i] == "*":
            parts.append("[^/]*")
            i += 1
        elif pattern[i] == "?":
            parts.append("[^/]")
            i += 1
        else:
            parts.append(re.escape(pattern[i]))
            i += 1
    return re.match("^" + "".join(parts) + "$", path) is not None
