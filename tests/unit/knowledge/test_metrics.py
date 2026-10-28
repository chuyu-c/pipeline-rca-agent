import pytest

from rca.knowledge.metrics import dedupe, recall_at_k, reciprocal_rank


def test_dedupe_keeps_first_occurrence_order() -> None:
    assert dedupe(["a", "b", "a", "c", "b"]) == ["a", "b", "c"]


def test_recall_at_k() -> None:
    ranked = ["x", "a", "y", "b", "z"]
    assert recall_at_k(ranked, ["a", "b"], 5) == 1.0
    assert recall_at_k(ranked, ["a", "b"], 2) == 0.5
    assert recall_at_k(ranked, ["q"], 5) == 0.0
    assert recall_at_k([], [], 5) == 1.0


def test_reciprocal_rank() -> None:
    assert reciprocal_rank(["x", "a", "b"], ["a", "b"]) == pytest.approx(0.5)
    assert reciprocal_rank(["a"], ["a"]) == 1.0
    assert reciprocal_rank(["x", "y"], ["a"]) == 0.0
