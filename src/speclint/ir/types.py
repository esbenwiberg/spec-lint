from __future__ import annotations

from dataclasses import dataclass, field
from pathlib import Path
from typing import TYPE_CHECKING

if TYPE_CHECKING:
    from ..semantic import Embedder


@dataclass(frozen=True)
class Heading:
    file: str
    line: int
    level: int
    text: str
    anchor: str


@dataclass(frozen=True)
class Link:
    file: str
    line: int
    text: str
    target: str
    is_internal: bool


@dataclass(frozen=True)
class Claim:
    """A MUST/SHALL/SHOULD/MAY statement or imperative bullet."""

    file: str
    line: int
    text: str
    modal: str | None
    has_verification_hook: bool


@dataclass(frozen=True)
class Term:
    text: str
    occurrences: tuple[tuple[str, int], ...]


@dataclass(frozen=True)
class Manifest:
    id: str
    status: str
    owner: str | None = None
    references: tuple[str, ...] = ()
    related: tuple[str, ...] = ()
    raw: dict | None = None

    @property
    def is_draft(self) -> bool:
        return self.status == "draft"


@dataclass
class SpecIR:
    folder: Path
    manifest: Manifest | None
    manifest_errors: list[str] = field(default_factory=list)
    files: list[str] = field(default_factory=list)
    headings: list[Heading] = field(default_factory=list)
    links: list[Link] = field(default_factory=list)
    claims: list[Claim] = field(default_factory=list)
    terms: list[Term] = field(default_factory=list)
    raw_text: dict[str, str] = field(default_factory=dict)
    # Path B coupling context. `repo_root` lets rules resolve repo-relative
    # globs from spec.yml. `changed_paths` is the set of repo-root-relative
    # paths modified in the current run (e.g., from `git diff`); None means
    # no diff context was provided — coupling rules MUST short-circuit.
    repo_root: Path | None = None
    changed_paths: tuple[str, ...] | None = None
    # Semantic-tier context. Set by the runner when any semantic rule is
    # loaded and an embedder is available. Semantic rules MUST short-circuit
    # if this is None — the runner will normally drop them before they ever
    # see the IR, but defensively guarding keeps unit-test paths safe.
    embedder: "Embedder | None" = None

    @property
    def name(self) -> str:
        return self.folder.name

    @property
    def status(self) -> str:
        return self.manifest.status if self.manifest else "unknown"

    @property
    def folder_relpath(self) -> str | None:
        """Spec folder path relative to repo_root, posix-style. None if no
        repo_root is set."""
        if self.repo_root is None:
            return None
        try:
            return self.folder.resolve().relative_to(self.repo_root.resolve()).as_posix()
        except ValueError:
            return None
