"""The agent's terminal output: `RCAReport` (DESIGN §6) and its closed set of remediation actions.

Actions are proposals only. Execution happens server-side after human approval (DESIGN §5.3).
"""

from datetime import date
from typing import Annotated, Literal

from pydantic import BaseModel, ConfigDict, Field

from rca.taxonomy import Category


class _Strict(BaseModel):
    model_config = ConfigDict(extra="forbid")


class RerunTask(_Strict):
    type: Literal["rerun_task"]
    task_id: str
    logical_date: date
    rationale: str = Field(max_length=500)


class BackfillPartition(_Strict):
    type: Literal["backfill_partition"]
    table: str = Field(description="schema.table to rebuild, e.g. mart.daily_gmv_by_region")
    logical_date: date
    rationale: str = Field(max_length=500)


class QuarantinePartition(_Strict):
    type: Literal["quarantine_partition"]
    table: str
    logical_date: date
    rationale: str = Field(max_length=500)


class RevertDeploy(_Strict):
    type: Literal["revert_deploy"]
    deploy_id: str
    rationale: str = Field(max_length=500)


class Escalate(_Strict):
    type: Literal["escalate"]
    team: str = Field(description="who should act, e.g. 'orders source team', 'data platform'")
    message: str = Field(max_length=1000)
    rationale: str = Field(max_length=500)


Action = Annotated[
    RerunTask | BackfillPartition | QuarantinePartition | RevertDeploy | Escalate,
    Field(discriminator="type"),
]


class Evidence(_Strict):
    tool_call_id: str = Field(description="id of the tool call whose output supports the claim")
    excerpt: str = Field(max_length=500, description="the exact supporting text from that output")


class RCAReport(_Strict):
    summary: str = Field(max_length=2000)
    category: Category
    root_entity: str = Field(
        description="table ('raw.orders'), column ('raw.orders.region'), task ('ingest_orders') "
        "or deploy ('deploy:d-42')"
    )
    confidence: float = Field(ge=0, le=1)
    evidence: list[Evidence] = Field(min_length=1)
    citations: list[str] = Field(default=[], description="knowledge chunk ids, e.g. runbooks/x#02")
    impacted_datasets: list[str] = []
    proposed_actions: list[Action] = []
    hypotheses_considered: list[str] = []
