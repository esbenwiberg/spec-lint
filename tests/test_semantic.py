"""Semantic-tier infrastructure: Embedder protocol, BagOfTokensEmbedder,
cosine similarity, fastembed availability probe, and the runner's
graceful-skip path when fastembed isn't installed."""
from __future__ import annotations

import math
from pathlib import Path

import pytest

from speclint.config import Config
from speclint.rules.registry import RuleRegistry
from speclint.rules.types import Finding, Rule
from speclint.runner import run
from speclint.semantic import (
    BagOfTokensEmbedder,
    Embedder,
    FastembedEmbedder,
    SemanticUnavailable,
    cosine_sim,
    fastembed_available,
)


# ---------- cosine_sim ----------

def test_cosine_sim_identical_is_one():
    assert cosine_sim([1.0, 2.0, 3.0], [1.0, 2.0, 3.0]) == pytest.approx(1.0)


def test_cosine_sim_orthogonal_is_zero():
    assert cosine_sim([1.0, 0.0], [0.0, 1.0]) == pytest.approx(0.0)


def test_cosine_sim_opposite_is_negative_one():
    assert cosine_sim([1.0, 0.0], [-1.0, 0.0]) == pytest.approx(-1.0)


def test_cosine_sim_zero_vector_returns_zero():
    assert cosine_sim([0.0, 0.0], [1.0, 1.0]) == 0.0
    assert cosine_sim([1.0, 1.0], [0.0, 0.0]) == 0.0


def test_cosine_sim_length_mismatch_raises():
    with pytest.raises(ValueError, match="length mismatch"):
        cosine_sim([1.0, 2.0], [1.0])


# ---------- BagOfTokensEmbedder ----------

def test_bag_of_tokens_is_an_embedder():
    assert isinstance(BagOfTokensEmbedder(), Embedder)


def test_bag_of_tokens_identical_inputs_produce_identical_vectors():
    e = BagOfTokensEmbedder()
    vecs = e.embed(["hello world", "hello world"])
    assert vecs[0] == vecs[1]


def test_bag_of_tokens_case_insensitive():
    e = BagOfTokensEmbedder()
    v_lower, v_upper = e.embed(["hello world", "HELLO WORLD"])
    assert v_lower == v_upper


def test_bag_of_tokens_order_invariant_for_same_token_set():
    """Bag-of-tokens by definition ignores order."""
    e = BagOfTokensEmbedder()
    v1, v2 = e.embed(["payment processing", "processing payment"])
    assert cosine_sim(v1, v2) == pytest.approx(1.0)


def test_bag_of_tokens_disjoint_inputs_are_orthogonal():
    e = BagOfTokensEmbedder()
    v1, v2 = e.embed(["authentication login", "database migration"])
    assert cosine_sim(v1, v2) == pytest.approx(0.0)


def test_bag_of_tokens_partial_overlap_in_expected_range():
    e = BagOfTokensEmbedder()
    v1, v2 = e.embed(["API Rate Limiting", "API Rate Limiting Strategy"])
    sim = cosine_sim(v1, v2)
    # 3 shared tokens out of 3+4 — should be high but not 1.0
    assert 0.85 < sim < 1.0


def test_bag_of_tokens_returns_aligned_vectors():
    """All output vectors must have the same length (shared vocab axis)."""
    e = BagOfTokensEmbedder()
    vecs = e.embed(["a b", "c d e", "f"])
    assert len({len(v) for v in vecs}) == 1


def test_bag_of_tokens_empty_input_safe():
    e = BagOfTokensEmbedder()
    vecs = e.embed(["", "hello"])
    # First vector is all-zero, but length matches the second
    assert len(vecs[0]) == len(vecs[1])
    assert all(v == 0.0 for v in vecs[0])


def test_bag_of_tokens_has_name():
    assert BagOfTokensEmbedder().name == "bag-of-tokens"


# ---------- FastembedEmbedder ----------

def test_fastembed_constructor_raises_when_unavailable(monkeypatch):
    """When fastembed isn't installed, constructing the wrapper must raise
    SemanticUnavailable with a pip-install hint — not a bare ImportError."""
    monkeypatch.setattr("speclint.semantic.fastembed_available", lambda: False)
    with pytest.raises(SemanticUnavailable, match="pip install"):
        FastembedEmbedder()


def test_fastembed_available_probe_does_not_import(monkeypatch):
    """The probe must use find_spec, not actually import. Verifies we
    don't pay the ~200MB ONNX init cost just to check availability."""
    # If fastembed isn't installed in this env, probe returns False.
    # If it is, probe returns True. Either way: the call must succeed
    # without raising and without populating sys.modules['fastembed.*'].
    import sys
    sentinel = "fastembed.embedding"
    pre_loaded = sentinel in sys.modules
    fastembed_available()
    assert (sentinel in sys.modules) == pre_loaded


# ---------- runner integration ----------

def _semantic_rule() -> Rule:
    """Synthetic semantic-tier rule that fires a finding only when an
    embedder is present — proves wiring without depending on fastembed."""
    def check(ir, config):
        if ir.embedder is None:
            return []
        # Use the embedder so the test confirms it's actually reachable
        _ = ir.embedder.embed(["smoke test"])
        return [Finding(rule_id="fake-semantic", severity="info",
                        file="x", line=1, message="ran semantic check",
                        spec=None)]

    return Rule(
        id="fake-semantic", version="1.0.0", tier="semantic",
        default_severity="info", rationale="test only",
        check=check, package="default",
    )


def _seed_repo(tmp_path: Path) -> Path:
    (tmp_path / "specs" / "demo").mkdir(parents=True)
    (tmp_path / "specs" / "demo" / "spec.yml").write_text("id: demo\nstatus: accepted\n")
    (tmp_path / "specs" / "demo" / "README.md").write_text("# demo\n## A\n## B\n")
    return tmp_path


def _patch_registry(monkeypatch, *rules: Rule) -> None:
    reg = RuleRegistry(
        rules={r.id: r for r in rules},
        overrides=[],
        package_order=["default"],
    )
    monkeypatch.setattr("speclint.runner.load_rule_packages", lambda _: reg)


def test_runner_drops_semantic_rules_when_fastembed_unavailable(tmp_path, monkeypatch):
    _seed_repo(tmp_path)
    _patch_registry(monkeypatch, _semantic_rule())
    monkeypatch.setattr("speclint.runner.fastembed_available", lambda: False)

    result = run(tmp_path, Config())
    assert result.rules_skipped_semantic == ["fake-semantic"]
    assert result.embedder_chosen is None
    # The synthetic rule must NOT have fired — it was dropped
    assert not any(f.rule_id == "fake-semantic" for f in result.findings)


def test_runner_runs_semantic_rules_when_embedder_available(tmp_path, monkeypatch):
    _seed_repo(tmp_path)
    _patch_registry(monkeypatch, _semantic_rule())

    class StubEmbedder:
        name = "stub"
        def embed(self, texts):
            return [[1.0] for _ in texts]

    monkeypatch.setattr("speclint.runner.fastembed_available", lambda: True)
    monkeypatch.setattr("speclint.runner.FastembedEmbedder", lambda: StubEmbedder())

    result = run(tmp_path, Config())
    assert result.rules_skipped_semantic == []
    assert result.embedder_chosen == "stub"
    assert any(f.rule_id == "fake-semantic" for f in result.findings)


def test_runner_skips_embedder_resolution_when_no_semantic_rules(tmp_path, monkeypatch):
    """No semantic rules → no FastembedEmbedder() ctor call, no probe."""
    _seed_repo(tmp_path)
    calls: list[int] = []

    def boom():
        calls.append(1)
        return True

    monkeypatch.setattr("speclint.runner.fastembed_available", boom)
    # Default registry has no semantic rules unless we add them; use empty
    _patch_registry(monkeypatch)  # registry with zero rules

    result = run(tmp_path, Config())
    assert calls == []
    assert result.embedder_chosen is None
    assert result.rules_skipped_semantic == []
