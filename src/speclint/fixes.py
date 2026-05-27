"""Apply auto-fix patches emitted by rules.

A ``Finding`` may carry a ``Patch`` describing a literal substring
replacement. The CLI's ``--fix`` flag triggers application: each patch
is matched against its target file's current content and rewritten.

Conflict rules:
- ``replace_all=False``: the ``old`` string must appear exactly once.
  Zero or multiple occurrences → skip with reason, leave file untouched.
- ``replace_all=True``: zero occurrences → skip; any other count → apply.
- Two patches targeting the same file are applied sequentially in
  finding order. If the second patch's ``old`` no longer matches (the
  first patch rewrote it), it's skipped as a conflict.
- Path-traversal: resolved targets must stay under ``repo_root``.
"""
from __future__ import annotations

from dataclasses import dataclass, field
from pathlib import Path

from .rules.types import Finding


@dataclass
class FixApplyResult:
    applied: list[tuple[str, str]] = field(default_factory=list)        # (path, rule_id)
    skipped: list[tuple[str, str, str]] = field(default_factory=list)   # (path, rule_id, reason)


def apply_fixes(repo_root: Path, findings: list[Finding]) -> FixApplyResult:
    """Apply patches from findings to disk. Idempotent over no-op patches;
    safe against patches whose `old` already vanished (skip + report).
    """
    result = FixApplyResult()
    file_cache: dict[Path, str] = {}
    repo_root_resolved = repo_root.resolve()

    for f in findings:
        if f.fix is None:
            continue
        patch = f.fix
        try:
            target = (repo_root / patch.path).resolve()
            target.relative_to(repo_root_resolved)
        except (OSError, ValueError):
            result.skipped.append((patch.path, f.rule_id, "path escapes repo root"))
            continue
        if not target.is_file():
            result.skipped.append((patch.path, f.rule_id, "file not found"))
            continue

        if target not in file_cache:
            try:
                file_cache[target] = target.read_text(encoding="utf-8")
            except OSError as e:
                result.skipped.append((patch.path, f.rule_id, f"read failed: {e}"))
                continue

        current = file_cache[target]
        count = current.count(patch.old)
        if count == 0:
            result.skipped.append(
                (patch.path, f.rule_id, "old string not present (already fixed or stale)")
            )
            continue
        if not patch.replace_all and count > 1:
            result.skipped.append(
                (patch.path, f.rule_id, f"old string appears {count}x; ambiguous (use replace_all)")
            )
            continue

        if patch.replace_all:
            file_cache[target] = current.replace(patch.old, patch.new)
        else:
            file_cache[target] = current.replace(patch.old, patch.new, 1)
        result.applied.append((patch.path, f.rule_id))

    # Flush rewritten files once at the end so a single failure doesn't
    # leave the repo half-patched.
    for path, content in file_cache.items():
        try:
            path.write_text(content, encoding="utf-8")
        except OSError as e:
            result.skipped.append((str(path), "<flush>", f"write failed: {e}"))

    return result


def count_fixable(findings: list[Finding]) -> int:
    return sum(1 for f in findings if f.fix is not None)
