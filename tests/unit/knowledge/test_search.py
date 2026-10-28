import pytest

from rca.knowledge.search import RRF_K, rrf


def test_rrf_scores_match_the_formula() -> None:
    fused = dict(rrf([["a", "b", "c"], ["b", "c", "d"]]))
    assert fused["a"] == pytest.approx(1 / (RRF_K + 1))
    assert fused["b"] == pytest.approx(1 / (RRF_K + 2) + 1 / (RRF_K + 1))
    assert fused["c"] == pytest.approx(1 / (RRF_K + 3) + 1 / (RRF_K + 2))
    assert fused["d"] == pytest.approx(1 / (RRF_K + 3))


def test_rrf_rewards_agreement_over_a_single_top_rank() -> None:
    order = [item for item, _ in rrf([["a", "b", "c"], ["b", "c", "d"]])]
    assert order == ["b", "c", "a", "d"]


def test_rrf_ties_break_by_first_appearance() -> None:
    # x and y each appear once at rank 1: equal scores.
    assert [i for i, _ in rrf([["x"], ["y"]])] == ["x", "y"]
    assert [i for i, _ in rrf([["y"], ["x"]])] == ["y", "x"]


def test_rrf_custom_k_and_edge_cases() -> None:
    assert dict(rrf([["a"]], k=0))["a"] == 1.0
    assert rrf([]) == []
    assert rrf([[], []]) == []
    single = rrf([["a", "b"]])
    assert [i for i, _ in single] == ["a", "b"]
