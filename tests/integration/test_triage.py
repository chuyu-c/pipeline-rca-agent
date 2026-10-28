"""Triage end to end on real scenario incidents: replayed agent run, and the fallback baseline."""

from collections.abc import Iterator
from datetime import UTC, datetime, timedelta
from pathlib import Path

import pytest

from rca.agent.llm import NoLLM, ReplayLLM
from rca.agent.loop import persist_outcome, run_triage
from rca.agent.tools import ToolContext
from rca.db import connect
from rca.faults.inject import inject, reset
from rca.faults.scenario import load_scenarios
from rca.knowledge.embed import HashEmbedder
from rca.knowledge.index import index_oncall
from rca.monitor.config import load_config
from rca.monitor.store import run_monitors
from tests.integration.conftest import (
    DAG_PATH,
    ScratchDb,
    build_lineage,
    run_on,
    scratch_database,
)

pytestmark = pytest.mark.integration

REPO = Path(__file__).parents[2]
SCENARIOS = load_scenarios(REPO / "eval" / "scenarios")
START = datetime(2026, 3, 2).date()
DAY = START + timedelta(days=8)
FIXTURES = REPO / "tests" / "fixtures" / "llm"

# What the deliberately simple fallback concludes (category, root entity). Misses are expected:
# they are what the agent has to improve on, and pinning them makes any baseline change visible.
BASELINE = {
    "schema_drift": ("schema_drift", "stg.order_items"),  # truth root: raw.order_items
    "injection": ("schema_drift", "stg.order_items"),
    "permission": ("permission", "stg.order_items"),  # truth root: raw.products
    "resource": ("resource", "raw.order_items"),  # truth root: stg_order_items
    "late_upstream": ("late_upstream", "raw.inventory_snapshots"),
    "null_spike": ("data_quality", "raw.orders.region"),
    "duplicate_load": ("duplicate_load", "raw.order_items"),  # truth root: ingest_orders
    "bad_deploy": ("duplicate_load", "stg.order_items"),  # truth: bad_deploy / deploy:d-1042
    "flaky_noise": ("unknown", "raw.order_items"),  # truth root: ingest_orders
}


@pytest.fixture(scope="module")
def env() -> Iterator[ScratchDb]:
    with scratch_database() as db:
        build_lineage(db)
        with connect(db.app_url, autocommit=True) as conn:
            index_oncall(conn, REPO / "oncall", HashEmbedder())
        for offset in range(8):
            assert run_on(db, START + timedelta(days=offset)).ok
        yield db


def open_incident(db: ScratchDb, scenario_id: str) -> int:
    with connect(db.admin_url, autocommit=True) as admin:
        reset(admin, SCENARIOS.values(), DAY, DAG_PATH.parent)
        inject(admin, SCENARIOS[scenario_id], DAY, DAG_PATH.parent)
    run_on(db, DAY)
    with connect(db.app_url, autocommit=True) as conn:
        results = run_monitors(
            conn, load_config(REPO / "monitors/datasets.yaml"), DAY, datetime.now(UTC)
        )
    assert len({r.incident_id for _, r in results}) == 1
    return results[0][1].incident_id


def test_replayed_agent_run_through_real_tools_is_persisted(env: ScratchDb) -> None:
    incident_id = open_incident(env, "schema_drift")
    replay = ReplayLLM(FIXTURES / "schema_drift.synthetic.json")
    with (
        connect(env.app_url, autocommit=True) as app,
        connect(env.readonly_url, autocommit=True) as ro,
    ):
        ctx = ToolContext(app, ro, HashEmbedder(), incident_id)
        outcome = run_triage(ctx, replay, max_steps=12, token_budget=400_000)
        persist_outcome(app, incident_id, outcome)
        row = app.execute(
            "SELECT rca, trace FROM ops.incidents WHERE incident_id = %s", (incident_id,)
        ).fetchone()

    assert replay.remaining == 0
    assert outcome.source == "agent", outcome.fallback_reason
    assert (outcome.report.category, outcome.report.root_entity) == (
        "schema_drift",
        "raw.order_items",
    )
    assert (outcome.tool_calls, outcome.guardrail_blocks) == (4, 0)

    tools = {e["tool_call_id"]: e for e in outcome.trace if e["type"] == "tool"}
    assert all(e["ok"] for e in tools.values())
    assert (
        '"price"' in tools["toolu_s3"]["content"]
    )  # the drift is visible through the readonly role
    assert "column i.unit_price does not exist" in tools["toolu_s1"]["content"]

    assert row is not None
    assert row["rca"]["source"] == "agent" and row["rca"]["root_entity"] == "raw.order_items"
    assert row["rca"]["usage"]["total"] == outcome.usage.total
    assert [e["type"] for e in row["trace"]][-1] == "result"


@pytest.mark.parametrize("scenario_id", sorted(SCENARIOS))
def test_fallback_baseline(env: ScratchDb, scenario_id: str) -> None:
    incident_id = open_incident(env, scenario_id)
    with (
        connect(env.app_url, autocommit=True) as app,
        connect(env.readonly_url, autocommit=True) as ro,
    ):
        ctx = ToolContext(app, ro, HashEmbedder(), incident_id)
        outcome = run_triage(ctx, NoLLM(), max_steps=12, token_budget=400_000)
        persist_outcome(app, incident_id, outcome)
        stored = app.execute(
            "SELECT rca->>'source' AS source FROM ops.incidents WHERE incident_id = %s",
            (incident_id,),
        ).fetchone()

    assert (
        outcome.source == "fallback" and outcome.fallback_reason == "LLM error: no LLM configured"
    )
    assert (outcome.report.category, outcome.report.root_entity) == BASELINE[scenario_id]
    assert stored == {"source": "fallback"}
