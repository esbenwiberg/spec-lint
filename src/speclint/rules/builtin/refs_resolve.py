"""Every `references:` glob in spec.yml must resolve to at least one real
file in the repo. Catches typos and globs that rotted as code moved.

Static — needs only the repo root (no diff context required)."""
from __future__ import annotations

from pathlib import Path
from typing import Any

from ...ir.types import SpecIR
from ..registry import rule
from ..types import ExpectedFinding, Finding, Fixture


_FIXTURES = [
    Fixture(
        name="references-resolve-passes",
        files={
            "spec.yml": "id: x\nstatus: accepted\nreferences:\n  - 'src/**'\n",
            "README.md": "# x\n",
        },
        repo_files={"src/example/auth.py": "def login(): ...\n"},
        expects=(),
    ),
    Fixture(
        name="rotted-glob-fires",
        files={
            "spec.yml": "id: x\nstatus: accepted\nreferences:\n  - 'src/gone/**'\n",
            "README.md": "# x\n",
        },
        repo_files={"src/here/still.py": "x = 1\n"},
        expects=(
            ExpectedFinding(file="spec.yml", message_contains="src/gone/**"),
        ),
    ),
    Fixture(
        name="multiple-rotted-globs-fire-separately",
        files={
            "spec.yml": (
                "id: x\nstatus: accepted\n"
                "references:\n  - 'src/a/**'\n  - 'src/b/**'\n  - 'src/c/**'\n"
            ),
            "README.md": "# x\n",
        },
        repo_files={"src/b/exists.py": "y = 2\n"},
        expects=(
            ExpectedFinding(message_contains="src/a/**"),
            ExpectedFinding(message_contains="src/c/**"),
        ),
    ),
    Fixture(
        name="no-references-no-findings",
        files={
            "spec.yml": "id: x\nstatus: accepted\n",
            "README.md": "# x\n",
        },
        expects=(),
    ),
    Fixture(
        name="missing-manifest-skips",
        files={"README.md": "# x\n"},
        expects=(),
    ),
]


@rule(
    id="refs-resolve",
    version="1.0.0",
    tier="static",
    default_severity="warn",
    rationale=(
        "A `references:` glob that matches zero files is dead weight — it "
        "either was a typo or it rotted as code moved. Either way it lies "
        "about coverage."
    ),
    fixtures=_FIXTURES,
)
def check(ir: SpecIR, config: dict[str, Any]) -> list[Finding]:
    if ir.manifest is None or not ir.manifest.references:
        return []
    if ir.repo_root is None:
        # No repo context — can't resolve globs. Stay silent rather than
        # firing false positives.
        return []

    severity = config.get("severity", "warn")
    findings: list[Finding] = []
    for glob in ir.manifest.references:
        if not _glob_has_match(ir.repo_root, glob):
            findings.append(
                Finding(
                    rule_id="refs-resolve",
                    severity=severity,
                    file="spec.yml",
                    line=None,
                    message=f"`references` glob matches no files: {glob}",
                    hint=(
                        "Remove the entry, fix the path, or move the spec "
                        "to status: deprecated."
                    ),
                )
            )
    return findings


def _glob_has_match(root: Path, pattern: str) -> bool:
    """True if `pattern` (repo-root-relative) matches any file or directory.

    `Path.glob` requires the pattern to be relative; we strip any leading
    slash defensively. Empty match = miss.
    """
    pattern = pattern.lstrip("/")
    if not pattern:
        return False
    try:
        return any(True for _ in root.glob(pattern))
    except (OSError, ValueError):
        return False
