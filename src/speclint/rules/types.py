from __future__ import annotations

from dataclasses import dataclass, field
from typing import Any, Callable, Literal, Protocol, runtime_checkable

Severity = Literal["error", "warn", "info", "off"]
Tier = Literal["static", "semantic", "llm"]

SEVERITY_RANK: dict[str, int] = {"off": -1, "info": 0, "warn": 1, "error": 2}


@dataclass(frozen=True)
class Finding:
    rule_id: str
    severity: Severity
    file: str
    line: int | None
    message: str
    hint: str | None = None
    spec: str | None = None  # spec folder name; filled by runner

    def to_dict(self) -> dict[str, Any]:
        return {
            "rule_id": self.rule_id,
            "severity": self.severity,
            "spec": self.spec,
            "file": self.file,
            "line": self.line,
            "message": self.message,
            "hint": self.hint,
        }


@dataclass(frozen=True)
class ExpectedFinding:
    """One expected finding from a fixture. All fields are matched as
    constraints; None means 'don't care'."""

    line: int | None = None
    message_contains: str | None = None
    file: str | None = None
    severity: Severity | None = None


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
