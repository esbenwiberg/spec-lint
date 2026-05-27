"""apply_fixes: covers happy path, ambiguity refusal, replace_all, missing
old (already fixed or stale), path-escape refusal, and multi-patch
sequencing."""
from __future__ import annotations

from pathlib import Path

from speclint.fixes import apply_fixes, count_fixable
from speclint.rules.types import Finding, Patch


def _f(path: str, old: str, new: str, replace_all: bool = False) -> Finding:
    return Finding(
        rule_id="test-rule",
        severity="info",
        file=path,
        line=None,
        message="x",
        fix=Patch(path=path, old=old, new=new, replace_all=replace_all),
    )


def test_unique_old_applied(tmp_path: Path):
    (tmp_path / "a.txt").write_text("hello world", encoding="utf-8")
    result = apply_fixes(tmp_path, [_f("a.txt", "hello", "goodbye")])
    assert (tmp_path / "a.txt").read_text() == "goodbye world"
    assert result.applied == [("a.txt", "test-rule")]
    assert result.skipped == []


def test_ambiguous_old_refused_without_replace_all(tmp_path: Path):
    (tmp_path / "a.txt").write_text("foo foo foo", encoding="utf-8")
    result = apply_fixes(tmp_path, [_f("a.txt", "foo", "bar")])
    assert (tmp_path / "a.txt").read_text() == "foo foo foo"
    assert result.applied == []
    assert len(result.skipped) == 1
    assert "ambiguous" in result.skipped[0][2]


def test_replace_all_rewrites_every_occurrence(tmp_path: Path):
    (tmp_path / "a.css").write_text(".x { y: 1; }\n.x-bar { z: 2; }\n", encoding="utf-8")
    result = apply_fixes(tmp_path, [_f("a.css", ".x", ".y", replace_all=True)])
    assert (tmp_path / "a.css").read_text() == ".y { y: 1; }\n.y-bar { z: 2; }\n"
    assert result.applied == [("a.css", "test-rule")]


def test_missing_old_skipped(tmp_path: Path):
    (tmp_path / "a.txt").write_text("hello", encoding="utf-8")
    result = apply_fixes(tmp_path, [_f("a.txt", "gone", "x")])
    assert (tmp_path / "a.txt").read_text() == "hello"
    assert result.applied == []
    assert "not present" in result.skipped[0][2]


def test_path_escape_refused(tmp_path: Path):
    result = apply_fixes(tmp_path, [_f("../escape.txt", "x", "y")])
    assert result.applied == []
    assert "escapes" in result.skipped[0][2]


def test_findings_without_fix_are_ignored(tmp_path: Path):
    plain = Finding(rule_id="r", severity="info", file="a", line=1, message="m")
    result = apply_fixes(tmp_path, [plain])
    assert result.applied == [] and result.skipped == []


def test_sequential_patches_on_same_file(tmp_path: Path):
    (tmp_path / "a.txt").write_text("alpha beta gamma", encoding="utf-8")
    result = apply_fixes(
        tmp_path,
        [_f("a.txt", "alpha", "ALPHA"), _f("a.txt", "gamma", "GAMMA")],
    )
    assert (tmp_path / "a.txt").read_text() == "ALPHA beta GAMMA"
    assert len(result.applied) == 2


def test_second_patch_conflicts_after_first_rewrites(tmp_path: Path):
    # First patch rewrites "x" → "z". Second patch's `old="x"` no longer
    # matches because the in-memory buffer holds "z". The conflict is
    # surfaced rather than silently re-rewriting.
    (tmp_path / "a.txt").write_text("x", encoding="utf-8")
    result = apply_fixes(
        tmp_path,
        [_f("a.txt", "x", "z"), _f("a.txt", "x", "y")],
    )
    assert (tmp_path / "a.txt").read_text() == "z"
    assert len(result.applied) == 1
    assert len(result.skipped) == 1


def test_count_fixable():
    fixable = Finding(rule_id="r", severity="info", file="a", line=1, message="m",
                      fix=Patch(path="a", old="x", new="y"))
    plain = Finding(rule_id="r", severity="info", file="a", line=1, message="m")
    assert count_fixable([fixable, plain, fixable]) == 2
