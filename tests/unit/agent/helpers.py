"""Shared fakes for agent unit tests: no database, no network."""

import json
from collections.abc import Mapping
from typing import Any

from rca.agent.schema import RCAReport
from rca.agent.tools import ToolContext, ToolResult, execute
from rca.knowledge.embed import HashEmbedder


class Untouchable:
    def __getattr__(self, name: str) -> Any:
        raise AssertionError(f"database used: .{name}")


def make_ctx() -> ToolContext:
    return ToolContext(Untouchable(), Untouchable(), HashEmbedder(), incident_id=7)  # type: ignore[arg-type]


CANNED: dict[str, Any] = {
    "get_incident": {"incident": {"incident_id": 7, "root_candidate": "stg.order_items"},
                     "alerts": [{"detector": "task_failure", "error_class": "UndefinedColumn"}]},
    "get_lineage": {"table": "stg.order_items", "tables": [{"table": "raw.order_items"}]},
    "get_task_runs": {"runs": [{"status": "failed", "error_class": "UndefinedColumn",
                                "error_message": "column i.unit_price does not exist"}]},
}  # fmt: skip


class FakeExecutor:
    """Canned data for the read tools; real validation and SQL guard for everything else (they
    reject before touching the database, which is all these tests send them)."""

    def __init__(self) -> None:
        self.calls: list[tuple[str, dict[str, Any]]] = []

    def __call__(self, name: str, raw: Mapping[str, Any], ctx: ToolContext) -> ToolResult:
        self.calls.append((name, dict(raw)))
        if name in CANNED and not (name == "get_lineage" and "depth" in raw and raw["depth"] > 3):
            result = ToolResult.success(CANNED[name])
            result.content = json.dumps({"ok": True, "data": CANNED[name]})
            return result
        return execute(name, raw, ctx)


class FakeFallback:
    def __init__(self) -> None:
        self.reasons: list[str] = []

    def __call__(self, ctx: ToolContext, reason: str) -> RCAReport:
        self.reasons.append(reason)
        return RCAReport(
            summary="heuristic", category="schema_drift", root_entity="stg.order_items",
            confidence=0.4, evidence=[{"tool_call_id": "fallback:incident", "excerpt": "x"}],  # type: ignore[list-item]
        )  # fmt: skip


def valid_report(evidence_id: str = "toolu_1") -> dict[str, Any]:
    return {
        "summary": "raw.order_items.unit_price was renamed upstream.",
        "category": "schema_drift",
        "root_entity": "raw.order_items",
        "confidence": 0.85,
        "evidence": [{"tool_call_id": evidence_id, "excerpt": "column i.unit_price missing"}],
        "citations": ["runbooks/schema_drift#01"],
        "impacted_datasets": ["stg.order_items"],
        "proposed_actions": [
            {"type": "escalate", "team": "orders source team", "message": "confirm the rename",
             "rationale": "upstream contract change"},
        ],
    }  # fmt: skip
