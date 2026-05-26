"""Path B coupling rule: if a changed file matches this spec's
references globs and nothing inside the spec folder was changed in the
same diff, the spec is drifting away from the code it covers.

Metadata-driven. Requires both (a) diff context (``SpecIR.changed_paths``)
and (b) a metadata sidecar with a references-like list. Short-circuits
silently otherwise."""
from __future__ import annotations

import re
from typing import Any

from ...ir.types import SpecIR
from ..registry import rule
from ..types import ExpectedFinding, Finding, Fixture


_FIXTURES = [
    Fixture(
        name="code-touched-spec-also-touched-passes",
        files={"README.md": "# x\n"},
        metadata={"references": ["src/x/**"]},
        repo_files={"src/x/a.py": "x = 1\n"},
        changed_paths=("src/x/a.py", "spec/README.md"),
        expects=(),
    ),
    Fixture(
        name="code-touched-spec-not-touched-fires",
        files={"README.md": "# x\n"},
        metadata={"references": ["src/x/**"]},
        repo_files={"src/x/a.py": "x = 1\n"},
        changed_paths=("src/x/a.py",),
        expects=(
            ExpectedFinding(file="contract.yaml", message_contains="src/x/a.py"),
        ),
    ),
    Fixture(
        name="unrelated-changes-do-not-fire",
        files={"README.md": "# x\n"},
        metadata={"references": ["src/x/**"]},
        repo_files={"src/y/b.py": "y = 2\n"},
        changed_paths=("src/y/b.py",),
        expects=(),
    ),
    Fixture(
        name="draft-status-skips",
        files={"README.md": "# x\n"},
        metadata={"references": ["src/x/**"], "status": "draft"},
        changed_paths=("src/x/a.py",),
        expects=(),
    ),
    Fixture(
        name="no-diff-context-short-circuits",
        files={"README.md": "# x\n"},
        metadata={"references": ["src/x/**"]},
        changed_paths=None,
        expects=(),
    ),
    Fixture(
        name="no-metadata-short-circuits",
        files={"README.md": "# x\n"},
        changed_paths=("src/x/a.py",),
        expects=(),
    ),
    Fixture(
        name="empty-diff-no-findings",
        files={"README.md": "# x\n"},
        metadata={"references": ["src/x/**"]},
        changed_paths=(),
        expects=(),
    ),
    Fixture(
        name="multiple-matched-files-collapse-to-one-finding",
        files={"README.md": "# x\n"},
        metadata={"references": ["src/x/**"]},
        changed_paths=("src/x/a.py", "src/x/b.py", "src/x/sub/c.py"),
        expects=(
            ExpectedFinding(file="contract.yaml", message_contains="3 file(s)"),
        ),
    ),
]


@rule(
    id="refs-coupling",
    version="2.0.0",
    tier="static",
    default_severity="warn",
    rationale=(
        "If you change code that a spec claims to cover, the spec should "
        "be reviewed in the same change. Otherwise specs silently drift "
        "away from the code they describe. Off unless metadata is "
        "configured and diff context is supplied."
    ),
    fixtures=_FIXTURES,
)
def check(ir: SpecIR, config: dict[str, Any]) -> list[Finding]:
    if ir.changed_paths is None:
        return []

    field = config.get("field", "references")
    references = ir.metadata.get(field)
    if not isinstance(references, list) or not references:
        return []

    status_field = config.get("status_field", "status")
    if ir.metadata.get(status_field) == "draft":
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

    string_refs = [g for g in references if isinstance(g, str)]
    matched = sorted(
        p for p in ir.changed_paths
        if any(_glob_match(g, p) for g in string_refs)
    )
    if not matched:
        return []

    severity = config.get("severity", "warn")
    preview = ", ".join(matched[:3])
    more = "" if len(matched) <= 3 else f" (+{len(matched) - 3} more)"
    sidecar_name = config.get("sidecar_filename", _guess_sidecar(ir))
    return [
        Finding(
            rule_id="refs-coupling",
            severity=severity,
            file=sidecar_name,
            line=None,
            message=(
                f"{len(matched)} file(s) under this spec's `{field}` "
                f"changed but the spec was not updated: {preview}{more}"
            ),
            hint=(
                "Update the spec in the same change, or mark it as "
                "deprecated in metadata if it no longer applies."
            ),
        )
    ]


def _guess_sidecar(ir: SpecIR) -> str:
    try:
        for p in ir.folder.iterdir():
            if p.suffix in {".yml", ".yaml"} and p.is_file():
                return p.name
    except OSError:
        pass
    return "<metadata>"


def _glob_match(pattern: str, path: str) -> bool:
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
