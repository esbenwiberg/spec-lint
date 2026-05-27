from __future__ import annotations

from dataclasses import dataclass, field
from typing import Any, Callable, Literal, Protocol, runtime_checkable

Severity = Literal["error", "warn", "info", "off"]
Tier = Literal["static", "semantic", "llm"]

SEVERITY_RANK: dict[str, int] = {"off": -1, "info": 0, "warn": 1, "error": 2}


@dataclass(frozen=True)
class Patch:
    """A single literal substring replacement applied to a repo file.

    ``path`` is repo-root-relative. ``old`` must appear in the file's
    current contents — when ``replace_all=False`` it must appear exactly
    once (we refuse ambiguous patches); when ``True`` every occurrence
    is rewritten (used for renames). Multi-line strings are fine; this
    is a literal find/replace, not regex.
    """

    path: str
    old: str
    new: str
    replace_all: bool = False

    def to_dict(self) -> dict[str, Any]:
        return {
            "path": self.path,
            "old": self.old,
            "new": self.new,
            "replace_all": self.replace_all,
        }


Anchor = Literal["spec", "repo"]


@dataclass(frozen=True)
class Finding:
    rule_id: str
    severity: Severity
    file: str
    line: int | None
    message: str
    hint: str | None = None
    spec: str | None = None  # spec folder name; filled by runner
    fix: Patch | None = None  # opt-in auto-fix; applied when CLI --fix is passed
    # Whether `file` is relative to the spec folder (default — most rules)
    # or to the repo root (rules that anchor to artifacts, e.g.
    # spec-impl-drift). Reporters use this to build clickable links.
    anchor: Anchor = "spec"

    def to_dict(self) -> dict[str, Any]:
        return {
            "rule_id": self.rule_id,
            "severity": self.severity,
            "spec": self.spec,
            "file": self.file,
            "line": self.line,
            "anchor": self.anchor,
            "message": self.message,
            "hint": self.hint,
            "fix": self.fix.to_dict() if self.fix else None,
        }


@dataclass(frozen=True)
class ExpectedFinding:
    """One expected finding from a fixture. All fields are matched as
    constraints; None means 'don't care'."""

    line: int | None = None
    message_contains: str | None = None
    file: str | None = None
    severity: Severity | None = None
    # True/False asserts on `fix` presence; None means don't care.
    has_fix: bool | None = None


@dataclass(frozen=True)
class Fixture:
    """A self-contained test case for a rule.

    ``files`` is a map of relative path -> content for the spec folder.
    The harness writes them into a tmp dir, builds the SpecIR, runs the
    rule, and asserts findings match ``expects`` (or that no findings
    fire when ``expects`` is empty).

    ``metadata`` is an optional dict written as a YAML sidecar named
    ``sidecar_filename`` (default ``contract.yaml``) and loaded into
    ``ir.metadata``. Use this when a rule reads metadata fields like
    ``status`` or ``references``. Leave empty to exercise the
    no-metadata path (most rules don't care).

    Path B fixtures additionally use:
      - ``repo_files``: files written at the repo root (paths relative
        to it, outside the spec folder) — for resolving references
        globs against repo state.
      - ``changed_paths``: repo-root-relative paths treated as "modified
        in this diff" — populates SpecIR.changed_paths. None means no
        diff context (coupling rules short-circuit); empty tuple means
        a diff ran but no files changed.
    """

    name: str
    files: dict[str, str]
    expects: tuple[ExpectedFinding, ...] = ()
    options: dict[str, Any] = field(default_factory=dict)
    repo_files: dict[str, str] = field(default_factory=dict)
    changed_paths: tuple[str, ...] | None = None
    metadata: dict[str, Any] = field(default_factory=dict)
    sidecar_filename: str = "contract.yaml"

    @property
    def expects_no_findings(self) -> bool:
        return len(self.expects) == 0


@runtime_checkable
class CheckFn(Protocol):
    def __call__(self, ir: Any, config: dict[str, Any]) -> list[Finding]: ...


@dataclass
class Rule:
    id: str
    version: str
    tier: Tier
    default_severity: Severity
    rationale: str
    check: Callable[..., list[Finding]]
    fixtures: list[Fixture] = field(default_factory=list)
    # Set when loaded so override audit can name the source package
    package: str | None = None
