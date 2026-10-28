from datetime import date
from typing import Any

import pytest

from rca.agent.fallback import IncidentFacts, classify, fallback_report

D = date(2026, 3, 10)


def facts(alerts: list[dict[str, Any]], failures: list[dict[str, Any]] | None = None,
          root: str = "stg.order_items") -> IncidentFacts:  # fmt: skip
    return IncidentFacts(1, root, D, alerts, failures or [])


def alert(detector: str, dataset: str, error_class: str, **payload: Any) -> dict[str, Any]:
    return {"detector": detector, "dataset": dataset, "error_class": error_class,
            "logical_date": D, "payload": payload}  # fmt: skip


def failure(error_class: str, message: str) -> dict[str, Any]:
    return {"task_id": "stg_order_items", "error_class": error_class, "error_message": message}


CASES = [
    ([], [failure("UndefinedColumn", "column i.unit_price does not exist")], "schema_drift"),
    ([], [failure("InsufficientPrivilege", "permission denied for table products")], "permission"),
    ([], [failure("QueryCanceled", "canceling statement due to statement timeout")], "resource"),
    ([], [failure("ZeroDivisionError", "division by zero")], "unknown"),
    ([alert("null_rate", "raw.orders", "null_rate:region")], [], "data_quality"),
    ([alert("freshness", "raw.inventory_snapshots", "partition_missing")], [], "late_upstream"),
    ([alert("volume", "raw.orders", "volume_high")], [], "duplicate_load"),
    ([alert("task_retry", "raw.order_items", "TimeoutError")], [], "unknown"),
]


@pytest.mark.parametrize(("alerts", "failures", "category"), CASES)
def test_regex_and_detector_classifier(
    alerts: list[dict[str, Any]], failures: list[dict[str, Any]], category: str
) -> None:
    assert classify(facts(alerts, failures))[0] == category


def test_failures_take_precedence_over_data_alerts() -> None:
    f = facts([alert("freshness", "mart.x", "partition_missing")],
              [failure("UndefinedColumn", "column x does not exist")])  # fmt: skip
    assert classify(f)[0] == "schema_drift"


def test_report_uses_topological_root_and_low_confidence() -> None:
    report = fallback_report(
        facts([alert("freshness", "mart.x", "partition_missing")],
              [failure("UndefinedColumn", "column i.unit_price does not exist")]),
        "LLM error: boom",
    )  # fmt: skip
    assert report.root_entity == "stg.order_items"  # the root candidate, not investigated further
    assert report.confidence == 0.4 and "LLM error: boom" in report.summary
    assert report.evidence[0].tool_call_id == "fallback:incident"
    assert report.proposed_actions[0].type == "escalate"


def test_data_quality_root_includes_the_column() -> None:
    report = fallback_report(
        facts([alert("null_rate", "raw.orders", "null_rate:region")], root="raw.orders"), "x"
    )
    assert report.root_entity == "raw.orders.region"
    assert report.proposed_actions[0].type == "quarantine_partition"


def test_unknown_has_no_actions() -> None:
    report = fallback_report(facts([alert("task_retry", "raw.order_items", "TimeoutError")]), "x")
    assert report.category == "unknown" and report.proposed_actions == []
    assert report.confidence == 0.2
