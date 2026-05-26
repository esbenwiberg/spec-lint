"""Resolve the set of changed files for Path B coupling rules.

Two sources, mutually exclusive:
  - A git ref (e.g., `origin/main`) — runs `git diff --name-only <base>...HEAD`
    using merge-base semantics so we ignore upstream commits that came in
    after branching.
  - A file containing newline-separated paths (escape hatch for CI runners
    that already know the changed file list, like GitHub Actions).

Returns a tuple of posix-style, repo-root-relative paths. None signals "no
diff context" — coupling rules treat that as silent skip, not zero-changes.
"""
from __future__ import annotations

import subprocess
from pathlib import Path

from .rules.registry import SpecLintError


def from_git(repo_root: Path, base: str) -> tuple[str, ...]:
    """Run `git diff --name-only <base>...HEAD` and return changed paths."""
    try:
        proc = subprocess.run(
            ["git", "-C", str(repo_root), "diff", "--name-only", f"{base}...HEAD"],
            capture_output=True,
            text=True,
            check=False,
        )
    except FileNotFoundError as e:
        raise SpecLintError(f"git not found on PATH: {e}") from e

    if proc.returncode != 0:
        raise SpecLintError(
            f"git diff failed (base={base}): {proc.stderr.strip() or 'unknown error'}"
        )

    return tuple(line.strip() for line in proc.stdout.splitlines() if line.strip())


def from_file(path: Path) -> tuple[str, ...]:
    """Read newline-separated paths from `path`. Blank lines + leading/
    trailing whitespace ignored."""
    if not path.exists():
        raise SpecLintError(f"--changed-files-from: file not found: {path}")
    text = path.read_text(encoding="utf-8")
    return tuple(line.strip() for line in text.splitlines() if line.strip())
