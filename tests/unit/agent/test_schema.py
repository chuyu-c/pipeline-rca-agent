from typing import Any

import pytest
from pydantic import ValidationError

from rca.agent.schema import RCAReport, RevertDeploy


def report(**overrides: Any) -> dict[str, Any]:
    base: dict[str, Any] = {
        "summary": "raw.order_items.unit_price was renamed upstream; stg_order_items fails.",
        "category": "schema_drift",
        "root_entity": "raw.order_items",
        "confidence": 0.9,
        "evidence": [{"tool_call_id": "toolu_1", "excerpt": "column i.unit_price does not exist"}],
        "citations": ["runbooks/schema_drift#01"],
        "impacted_datasets": ["stg.order_items", "mart.daily_gmv_by_region"],
        "proposed_actions": [
            {"type": "escalate", "team": "orders source team", "message": "confirm rename",
             "rationale": "contract change"},
            {"type": "revert_deploy", "deploy_id": "d-1042", "rationale": "suspect"},
        ],
        "hypotheses_considered": ["bad deploy", "schema drift"],
    }  # fmt: skip
    return {**base, **overrides}


def test_valid_report_parses_actions_by_type() -> None:
    parsed = RCAReport.model_validate(report())
    assert isinstance(parsed.proposed_actions[1], RevertDeploy)


@pytest.mark.parametrize(
    "overrides",
    [
        {"category": "aliens"},
        {"confidence": 1.5},
        {"evidence": []},  # every report needs evidence
        {"proposed_actions": [{"type": "drop_table", "rationale": "x"}]},
        {"proposed_actions": [{"type": "rerun_task", "rationale": "missing task and date"}]},
        {"extra_field": True},
    ],
)
def test_invalid_reports_are_rejected(overrides: dict[str, Any]) -> None:
    with pytest.raises(ValidationError):
        RCAReport.model_validate(report(**overrides))
