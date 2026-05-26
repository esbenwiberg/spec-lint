"""Spec discovery — find what to lint.

A *spec* is one of two things:

  - A folder containing one or more direct ``.md`` files. Its body is
    just those direct ``.md`` files (sub-folders are not pulled in;
    they become their own specs if they have markdown).
  - A ``.md`` file sitting directly under a known root, with no folder
    of its own. Common for ADR-style and PEP-style flat layouts (if a
    user opts those in via ``extra_roots``).

Discovery walks the curated default roots plus any ``extra_roots`` the
user added. It is intentionally heuristic and does not require any
manifest file — manifests are a rule-level concern, not a discovery
concern. This is the whole point: speclint adapts to *your* spec
layout, not the other way around."""
from __future__ import annotations

import fnmatch
from dataclasses import dataclass
from pathlib import Path

# Known paths where specs live across the SDD ecosystem. Each entry traces
# to either a real framework convention or a widely-used community pattern.
# Adding to this list = changing the zero-config default for every user, so
# every addition needs a real-world justification.
DEFAULT_ROOTS: tuple[str, ...] = (
    "specs",
    "spec",
    "docs/specs",
    "docs/spec",
    ".kiro/specs",        # AWS Kiro
    "openspec/specs",     # OpenSpec
    "openspec/changes",   # OpenSpec (proposal tree)
    "features",           # Cucumber / Gherkin
    "feats",
    "docs/features",
    "docs/feats",
    "briefs",
    "docs/briefs",
    "rfcs",
    "docs/rfcs",
)


@dataclass(frozen=True)
class SpecCandidate:
    """One thing to lint. ``name`` is what appears in reports."""

    name: str
    folder: Path
    md_files: tuple[Path, ...]   # relative-to-folder doesn't apply for single-file
    is_single_file: bool

    @property
    def file_relpaths(self) -> tuple[str, ...]:
        """File paths relative to ``folder`` — used by the IR builder."""
        if self.is_single_file:
            return (self.md_files[0].name,)
        return tuple(f.relative_to(self.folder).as_posix() for f in self.md_files)


def discover_specs(
    repo_root: Path,
    *,
    roots: tuple[str, ...] = DEFAULT_ROOTS,
    extra_roots: tuple[str, ...] = (),
    ignore: tuple[str, ...] = (),
) -> list[SpecCandidate]:
    """Walk the given roots under ``repo_root`` and return spec candidates.

    The discovery rule:

      1. For each root path under ``repo_root`` that exists as a directory,
         visit every directory beneath it recursively.
      2. A directory containing direct ``.md`` files becomes one spec —
         its body is *only* those direct ``.md`` files. Subdirectories are
         handled by recursion (they become their own specs if they have
         their own ``.md`` files).
      3. ``.md`` files sitting directly under a root that aren't already
         claimed by a folder spec become single-file specs.

    The ``ignore`` patterns apply to paths relative to ``repo_root`` and
    use ``fnmatch`` semantics — useful for skipping ``CHANGELOG.md``,
    vendored docs, etc.
    """
    repo_root = repo_root.resolve()
    seen: set[Path] = set()
    candidates: list[SpecCandidate] = []

    for rel_root in (*roots, *extra_roots):
        root_path = (repo_root / rel_root).resolve()
        if not root_path.is_dir():
            continue
        try:
            root_path.relative_to(repo_root)
        except ValueError:
            # Configured root escapes repo_root (e.g. via "../"). Skip.
            continue

        for spec in _walk_root(root_path, repo_root, ignore):
            if spec.folder in seen and not spec.is_single_file:
                continue
            if not spec.is_single_file:
                seen.add(spec.folder)
            candidates.append(spec)

    candidates.sort(key=lambda c: (c.folder.as_posix(), c.name))
    return candidates


def _walk_root(root: Path, repo_root: Path, ignore: tuple[str, ...]) -> list[SpecCandidate]:
    out: list[SpecCandidate] = []
    for folder, subdirs, filenames in _walk(root):
        # Honour ignore patterns at folder level
        rel_folder = folder.relative_to(repo_root).as_posix()
        if _ignored(rel_folder, ignore):
            subdirs.clear()
            continue

        md_files = sorted(
            folder / f for f in filenames
            if f.endswith(".md") and not _ignored(
                (folder / f).relative_to(repo_root).as_posix(), ignore
            )
        )

        if not md_files:
            continue

        if folder == root:
            # Direct .md children of a root: each becomes its own single-file
            # spec. This is the ADR/PEP-style flat layout. Subfolders are
            # handled by the recursion below.
            for md in md_files:
                out.append(SpecCandidate(
                    name=md.stem,
                    folder=folder,
                    md_files=(md,),
                    is_single_file=True,
                ))
        else:
            out.append(SpecCandidate(
                name=folder.name,
                folder=folder,
                md_files=tuple(md_files),
                is_single_file=False,
            ))
    return out


def _walk(path: Path):
    """Like os.walk but yields (Path, list[str], list[str]). We re-roll
    so we can yield Path objects directly and let callers mutate subdirs."""
    try:
        entries = list(path.iterdir())
    except OSError:
        return
    subdirs = sorted(e.name for e in entries if e.is_dir() and not e.name.startswith("."))
    files = sorted(e.name for e in entries if e.is_file())
    yield path, subdirs, files
    for sd in subdirs:
        yield from _walk(path / sd)


def _ignored(rel_path: str, patterns: tuple[str, ...]) -> bool:
    return any(fnmatch.fnmatch(rel_path, p) for p in patterns)
