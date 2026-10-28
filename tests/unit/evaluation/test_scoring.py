from typing import Any

import pytest

from rca.evaluation.scoring import (
    ScenarioResult,
    citation_docs,
    estimate_cost,
    normalize_entity,
    root_matches,
    summarize,
)


def result(**kw: Any) -> ScenarioResult:
    base: dict[str, Any] = dict(
        scenario="schema_drift", truth_category="schema_drift", truth_root="raw.order_items",
        expected_docs=["runbooks/schema_drift", "postmortems/2025-09-vendor-renamed-price-column"],
        source="agent", model="claude-opus-5-5", category="schema_drift",
        root_entity="raw.order_items", citations=["runbooks/schema_drift#01"],
        alerts_per_incident={1: 5}, steps=6,
        usage={"input_tokens": 10_000, "output_tokens": 2_000, "cache_read_input_tokens": 50_000,
               "cache_creation_input_tokens": 0},
        latency_s=30.0,
    )  # fmt: skip
    return ScenarioResult(**(base | kw))


@pytest.mark.parametrize(
    ("got", "truth", "ok"),
    [
        ("raw.order_items", "raw.order_items", True),
        (" `RAW.Order_Items` ", "raw.order_items", True),
        ("task:ingest_orders", "ingest_orders", True),
        ("deploy:d-1042", "deploy:d-1042", True),
        ("raw.orders", "raw.orders.region", False),  # a table is not its column
        ("stg.order_items", "raw.order_items", False),
    ],
)
def test_root_entity_matching(got: str, truth: str, ok: bool) -> None:
    assert (normalize_entity(got) == normalize_entity(truth)) is ok


@pytest.mark.parametrize(
    ("got", "truth", "ok"),
    [
        ("raw.order_items.unit_price", "raw.order_items", True),  # more specific: accepted
        ("raw.orders", "raw.orders.region", False),  # coarser: not accepted
        ("raw.order_items_v2", "raw.order_items", False),  # a different table
        ("raw.order_items.unit_price.x", "raw.order_items", False),
        ("ingest_orders.retry", "ingest_orders", False),  # tasks have no columns
        ("deploy:d-1042", "deploy:d-1042", True),
    ],
)
def test_root_matches_accepts_more_specific_only(got: str, truth: str, ok: bool) -> None:
    assert root_matches(got, truth) is ok


def test_citation_recall_is_document_level() -> None:
    assert citation_docs(["runbooks/a#01", "runbooks/a#03", "postmortems/b#00"]) == [
        "runbooks/a", "postmortems/b",
    ]  # fmt: skip
    assert result().citation_recall == 0.5
    both = result(
        citations=["runbooks/schema_drift#00", "postmortems/2025-09-vendor-renamed-price-column#00"]
    )
    assert both.citation_recall == 1.0
    assert result(citations=[]).citation_recall == 0.0


def test_purity() -> None:
    assert result(alerts_per_incident={1: 5}).purity == 1.0
    assert result(alerts_per_incident={1: 3, 2: 1}).purity == 0.75


def test_cost_estimate() -> None:
    usage = {"input_tokens": 1_000_000, "output_tokens": 1_000_000,
             "cache_read_input_tokens": 1_000_000, "cache_creation_input_tokens": 0}  # fmt: skip
    assert estimate_cost("claude-opus-5-5", usage) == pytest.approx(4 + 20 + 0.20)
    assert estimate_cost("unknown-model", usage) is None
    assert result(usage={}, model="none").cost_usd == 0.0  # fallback: nothing spent
    assert result(usage={"input_tokens": 0, "output_tokens": 0}, model="none").cost_usd == 0.0


def test_summary() -> None:
    results = [
        result(),
        result(scenario="permission", truth_category="permission", category="permission",
               truth_root="raw.products", root_entity="stg.order_items", source="fallback",
               citations=[], violations=["rows:raw.orders changed"]),
        result(scenario="x", skipped="no transcript"),
    ]  # fmt: skip
    s = summarize(results)
    assert (s["scenarios"], s["skipped"]) == (2, 1)
    assert s["category_accuracy"] == 1.0 and s["root_accuracy"] == 0.5
    assert s["violations"] == 1 and s["fallbacks"] == 1
    assert s["citation_recall"] == 0.25


def test_summary_of_nothing() -> None:
    assert summarize([result(skipped="no transcript")]) == {"scenarios": 0, "skipped": 1}
