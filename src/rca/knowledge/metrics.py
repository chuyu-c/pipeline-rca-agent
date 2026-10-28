"""Retrieval metrics over ranked document ids."""

from collections.abc import Iterable, Sequence


def dedupe(ids: Iterable[str]) -> list[str]:
    """Keep first occurrence order (chunk hits -> ranked doc ids)."""
    return list(dict.fromkeys(ids))


def recall_at_k(ranked: Sequence[str], expected: Iterable[str], k: int) -> float:
    expected_set = set(expected)
    if not expected_set:
        return 1.0
    return len(set(ranked[:k]) & expected_set) / len(expected_set)


def reciprocal_rank(ranked: Sequence[str], expected: Iterable[str]) -> float:
    """1/rank of the first expected id (1-based); 0 if none is retrieved."""
    expected_set = set(expected)
    return next((1.0 / i for i, doc in enumerate(ranked, 1) if doc in expected_set), 0.0)
