from datetime import UTC, date, datetime
from typing import Any

from rca.agent.show import render_incident

INCIDENT: dict[str, Any] = {
    "incident_id": 3, "status": "open", "root_candidate": "stg.order_items",
    "created_at": datetime(2026, 3, 10, 6, 0, tzinfo=UTC), "rca": None, "trace": None,
}  # fmt: skip
ALERTS = [
    {"detector": "task_failure", "dataset": "stg.order_items", "error_class": "UndefinedColumn",
     "severity": "critical", "logical_date": date(2026, 3, 10), "count": 1},
]  # fmt: skip
RCA = {
    "category": "permission", "root_entity": "raw.products", "confidence": 0.85,
    "summary": "SELECT on raw.products was revoked from rca_app.",
    "evidence": [{"tool_call_id": "toolu_1", "excerpt": "permission denied for table products"},
                 {"tool_call_id": "ctx-incident", "excerpt": "InsufficientPrivilege"}],
    "citations": ["runbooks/permission#01"],
    "proposed_actions": [{"type": "escalate", "team": "data platform", "message": "restore grant",
                          "rationale": "grants are owned by the platform team"}],
    "hypotheses_considered": ["schema drift"],
    "source": "agent", "model": "claude-opus-5-5", "steps": 4, "tool_calls": 2,
    "usage": {"total": 49859}, "duration_s": 45.9, "fallback_reason": None,
}  # fmt: skip
TRACE = [
    {"type": "context", "tool_call_id": "ctx-incident", "name": "get_incident"},
    {"type": "tool", "step": 1, "tool_call_id": "toolu_1", "name": "get_task_logs",
     "input": {"run_id": "r1"}, "ok": True, "blocked_reason": None},
    {"type": "tool", "step": 2, "tool_call_id": "toolu_2", "name": "run_readonly_sql",
     "input": {"sql": "SELECT * FROM ops.task_runs"}, "ok": False,
     "blocked_reason": "schema_not_allowed"},
    {"type": "repair", "step": 3, "problems": "evidence[0] cites 'x', which was never called"},
]  # fmt: skip


def test_untriaged_incident() -> None:
    md = render_incident(INCIDENT, ALERTS)
    assert md.startswith("# Incident #3\n")
    assert "| critical | task_failure | `stg.order_items` | UndefinedColumn |" in md
    assert "_Not triaged yet._" in md


def test_triaged_incident_report() -> None:
    md = render_incident(INCIDENT | {"rca": RCA, "trace": TRACE}, ALERTS)
    assert md.startswith("# Incident #3: permission at `raw.products`")
    assert "Triage: **agent** · model `claude-opus-5-5` · 4 steps" in md
    assert "49,859 tokens" in md
    # Evidence is attributed to the tool that produced it.
    assert "- `get_task_logs` (toolu_1): permission denied for table products" in md
    assert "- `get_incident` (ctx-incident): InsufficientPrivilege" in md
    assert "- **escalate** team=data platform, message=restore grant: grants are owned" in md
    assert "| 2 | `run_readonly_sql` | " in md and "blocked: schema_not_allowed" in md
    assert "| 3 | repair |" in md


def test_fallback_and_no_actions() -> None:
    rca = RCA | {"source": "fallback", "fallback_reason": "LLM error: x", "proposed_actions": []}
    md = render_incident(INCIDENT | {"rca": rca, "trace": []}, ALERTS)
    assert "fallback reason: LLM error: x" in md
    assert "## Proposed actions (need human approval)\n\n- none" in md
