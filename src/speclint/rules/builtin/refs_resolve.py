"""Every references-style glob in a spec's metadata must resolve to at
least one real file in the repo. Catches typos and globs that rotted
as code moved.

Metadata-driven: this rule only fires when the user configures a
metadata sidecar (e.g. ``metadata.sidecar: contract.yaml``) AND that
sidecar has a list-of-strings field (default ``references``). No
opinions about the schema — the field name is configurable via the
``field`` option."""
from __future__ import annotations

from pathlib import Path
from typing import Any

from ...ir.types import SpecIR
from ..registry import rule
from ..types import ExpectedFinding, Finding, Fixture


_FIXTURES = [
    Fixture(
        name="references-resolve-passes",
        files={"README.md": "# x\n"},
        metadata={"references": ["src/**"]},
        repo_files={"src/example/auth.py": "def login(): ...\n"},
        expects=(),
    ),
    Fixture(
        name="rotted-glob-fires",
        files={"README.md": "# x\n"},
        metadata={"references": ["src/gone/**"]},
        repo_files={"src/here/still.py": "x = 1\n"},
        expects=(
            ExpectedFinding(file="contract.yaml", message_contains="src/gone/**"),
        ),
    ),
    Fixture(
        name="multiple-rotted-globs-fire-separately",
        files={"README.md": "# x\n"},
        metadata={"references": ["src/a/**", "src/b/**", "src/c/**"]},
        repo_files={"src/b/exists.py": "y = 2\n"},
        expects=(
            ExpectedFinding(message_contains="src/a/**"),
            ExpectedFinding(message_contains="src/c/**"),
        ),
    ),
    Fixture(
        name="no-metadata-no-findings",
        files={"README.md": "# x\n"},
        expects=(),
    ),
    Fixture(
        name="metadata-without-field-no-findings",
        files={"README.md": "# x\n"},
        metadata={"status": "accepted"},
        expects=(),
    ),
    Fixture(
        name="custom-field-name",
        files={"README.md": "# x\n"},
        metadata={"owns": ["lib/nope/**"]},
        repo_files={"lib/here/x.py": "x = 1\n"},
        options={"field": "owns"},
        expects=(
            ExpectedFinding(message_contains="lib/nope/**"),
        ),
    ),
]


@rule(
    id="refs-resolve",
    version="2.0.0",
    tier="static",
    default_severity="warn",
    rationale=(
        "A references glob that matches zero files is dead weight — it "
        "either was a typo or it rotted as code moved. Either way it "
        "lies about coverage. Off unless metadata is configured."
    ),
    fixtures=_FIXTURES,
)
def check(ir: SpecIR, config: dict[str, Any]) -> list[Finding]:
    field = config.get("field", "references")
    references = ir.metadata.get(field)
    if not isinstance(references, list) or not references:
        return []
    if ir.repo_root is None:
        return []

    severity = config.get("severity", "warn")
    sidecar_name = config.get("sidecar_filename", _guess_sidecar(ir))
    findings: list[Finding] = []
    for glob in references:
        if not isinstance(glob, str):
            continue
        if not _glob_has_match(ir.repo_root, glob):
            findings.append(
                Finding(
                    rule_id="refs-resolve",
                    severity=severity,
                    file=sidecar_name,
                    line=None,
                    message=f"`{field}` glob matches no files: {glob}",
                    hint=(
                        "Remove the entry, fix the path, or update the "
                        "spec to reflect what code it actually owns."
                    ),
                )
            )
    return findings


def _guess_sidecar(ir: SpecIR) -> str:
    """Best-effort sidecar filename for the finding's `file` field.
    Looks at non-markdown files in the spec folder; falls back to a
    generic label if we can't tell."""
    try:
        for p in ir.folder.iterdir():
            if p.suffix in {".yml", ".yaml"} and p.is_file():
                return p.name
    except OSError:
        pass
    return "<metadata>"


def _glob_has_match(root: Path, pattern: str) -> bool:
    pattern = pattern.lstrip("/")
    if not pattern:
        return False
    try:
        return any(True for _ in root.glob(pattern))
    except (OSError, ValueError):
        return False
