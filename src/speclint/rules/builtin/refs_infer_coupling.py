"""Path B coupling rule (inferred from prose).

Sibling to ``refs-coupling``. That rule reads paths from a metadata sidecar;
this one scans the spec's markdown for path-shaped tokens, filters to paths
that actually exist in the repo, and fires when any of those files appear
in the diff while the spec folder itself is untouched.

Trade-off vs ``refs-coupling``: zero setup, but inherently noisier — prose
mentions are fuzzier than declared contracts. Defaults to ``info`` severity
for that reason. The "must exist in the repo" filter is what keeps the
signal usable: imaginary paths, glob patterns, and URL fragments all drop
out before we ever compare to the diff."""
from __future__ import annotations

import re
from typing import Any

from ...ir.types import SpecIR
from ..registry import rule
from ..types import ExpectedFinding, Finding, Fixture


# Strip fenced code blocks before scanning. Code examples routinely
# mention paths that aren't real ("import foo from './bar'") and were
# never claims about the repo state, so we drop them entirely.
_FENCE_RE = re.compile(r"```.*?```", re.DOTALL)

# Tokenizer: whitespace plus the prose punctuation that wraps paths.
# Backticks split here so `src/foo.py` becomes a bare token — that's
# the highest-signal mention in real specs.
_TOKEN_SPLIT_RE = re.compile(r"[\s`<>()\[\]{}'\",;:!?]+")

_URL_SCHEME_RE = re.compile(r"^[a-z][a-z0-9+.-]*://", re.IGNORECASE)

# Allow only "normal" extensions (2–6 ASCII alnum chars). Filters out
# version markers like `1.2.3` and trailing-dot prose noise.
_EXT_RE = re.compile(r"\.[A-Za-z0-9]{1,8}$")


def _extract_path_candidates(text: str) -> set[str]:
    """Return all path-shaped tokens in `text`, fenced code stripped."""
    text = _FENCE_RE.sub("", text)
    out: set[str] = set()
    for raw in _TOKEN_SPLIT_RE.split(text):
        if not raw:
            continue
        tok = raw.strip(".,/")
        if not tok or tok.startswith("/"):
            continue
        if _URL_SCHEME_RE.match(tok):
            continue
        if "/" not in tok:
            continue
        last = tok.rsplit("/", 1)[-1]
        if not _EXT_RE.search(last):
            continue
        # Glob patterns are a different shape — leave them to refs-resolve.
        if "*" in tok or "?" in tok:
            continue
        out.add(tok)
    return out


_FIXTURES = [
    Fixture(
        name="prose-path-mentioned-and-changed-fires",
        files={"README.md": "Touches `src/foo.py` for auth.\n"},
        repo_files={"src/foo.py": "x = 1\n"},
        changed_paths=("src/foo.py",),
        expects=(
            ExpectedFinding(file="README.md", message_contains="src/foo.py"),
        ),
    ),
    Fixture(
        name="prose-path-not-changed-passes",
        files={"README.md": "Touches `src/foo.py` for auth.\n"},
        repo_files={"src/foo.py": "x = 1\n", "src/other.py": "y = 2\n"},
        changed_paths=("src/other.py",),
        expects=(),
    ),
    Fixture(
        name="prose-path-not-in-repo-no-finding",
        files={"README.md": "Touches `src/imaginary.py`.\n"},
        repo_files={"src/real.py": "z = 3\n"},
        changed_paths=("src/real.py",),
        expects=(),
    ),
    Fixture(
        name="path-inside-fenced-code-block-ignored",
        files={
            "README.md": (
                "Don't read paths from code examples:\n"
                "```python\n"
                "open('src/foo.py')\n"
                "```\n"
                "End.\n"
            ),
        },
        repo_files={"src/foo.py": "x = 1\n"},
        changed_paths=("src/foo.py",),
        expects=(),
    ),
    Fixture(
        name="url-with-path-not-treated-as-repo-path",
        files={"README.md": "See https://example.com/foo.py for details.\n"},
        repo_files={"foo.py": "x = 1\n"},
        changed_paths=("foo.py",),
        expects=(),
    ),
    Fixture(
        name="glob-pattern-in-prose-ignored",
        files={"README.md": "Covers `src/**/*.py`.\n"},
        repo_files={"src/auth/x.py": "x = 1\n"},
        changed_paths=("src/auth/x.py",),
        expects=(),
    ),
    Fixture(
        name="spec-touched-short-circuits",
        files={"README.md": "Touches `src/foo.py`.\n"},
        repo_files={"src/foo.py": "x = 1\n"},
        changed_paths=("src/foo.py", "spec/README.md"),
        expects=(),
    ),
    Fixture(
        name="no-diff-context-short-circuits",
        files={"README.md": "Touches `src/foo.py`.\n"},
        repo_files={"src/foo.py": "x = 1\n"},
        changed_paths=None,
        expects=(),
    ),
    Fixture(
        name="draft-status-skips",
        files={"README.md": "Touches `src/foo.py`.\n"},
        metadata={"status": "draft"},
        repo_files={"src/foo.py": "x = 1\n"},
        changed_paths=("src/foo.py",),
        expects=(),
    ),
    Fixture(
        name="multiple-mentions-collapse-to-one-finding",
        files={
            "README.md": (
                "Affects `src/a.py`, `src/b.py`, and `src/c.py`.\n"
            ),
        },
        repo_files={
            "src/a.py": "x = 1\n",
            "src/b.py": "y = 2\n",
            "src/c.py": "z = 3\n",
        },
        changed_paths=("src/a.py", "src/b.py", "src/c.py"),
        expects=(
            ExpectedFinding(file="README.md", message_contains="3 file(s)"),
        ),
    ),
]


@rule(
    id="refs-infer-coupling",
    version="1.0.0",
    tier="static",
    default_severity="info",
    rationale=(
        "Specs often mention specific files in prose even when there's "
        "no metadata sidecar. If a mentioned file changes but the spec "
        "doesn't, the spec may be drifting. Noisier than refs-coupling "
        "(no declared contract) — defaults to info severity. Only "
        "flags paths that actually exist in the repo."
    ),
    fixtures=_FIXTURES,
)
def check(ir: SpecIR, config: dict[str, Any]) -> list[Finding]:
    if ir.changed_paths is None:
        return []
    if ir.repo_root is None:
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

    candidates: set[str] = set()
    for content in ir.raw_text.values():
        candidates |= _extract_path_candidates(content)
    if not candidates:
        return []

    repo_root = ir.repo_root
    real = {p for p in candidates if (repo_root / p).is_file()}
    if not real:
        return []

    matched = sorted(real & set(ir.changed_paths))
    if not matched:
        return []

    severity = config.get("severity", "info")
    preview = ", ".join(matched[:3])
    more = "" if len(matched) <= 3 else f" (+{len(matched) - 3} more)"
    anchor_file = next(iter(ir.raw_text.keys()), "<spec>")
    return [
        Finding(
            rule_id="refs-infer-coupling",
            severity=severity,
            file=anchor_file,
            line=None,
            message=(
                f"{len(matched)} file(s) mentioned in this spec changed "
                f"but the spec was not updated: {preview}{more}"
            ),
            hint=(
                "Update the spec to reflect the change, or drop the "
                "mention if it's no longer relevant. Add a metadata "
                "sidecar with refs-coupling for stricter coverage."
            ),
        )
    ]
