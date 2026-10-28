"""Agent tools against a real schema_drift incident, plus the rca_readonly second layer."""

import json
from collections.abc import Iterator
from datetime import UTC, datetime, timedelta
from pathlib import Path
from typing import Any

import psycopg
import pytest

from rca.agent.tools import ToolContext, execute
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
START = datetime(2026, 3, 2).date()
DAY = START + timedelta(days=8)


@pytest.fixture(scope="module")
def incident() -> Iterator[tuple[ScratchDb, int]]:
    scenarios = load_scenarios(REPO / "eval" / "scenarios")
    with scratch_database() as db:
        build_lineage(db)
        with connect(db.app_url, autocommit=True) as conn:
            index_oncall(conn, REPO / "oncall", HashEmbedder())
        for offset in range(8):
            assert run_on(db, START + timedelta(days=offset)).ok
        with connect(db.admin_url, autocommit=True) as admin:
            reset(admin, scenarios.values(), DAY, DAG_PATH.parent)
            inject(admin, scenarios["schema_drift"], DAY, DAG_PATH.parent)
            admin.execute(
                "INSERT INTO ops.deploys VALUES ('d-7', now() - interval '3 hours', 'dev.user2',"
                " 'stg_orders', 'abc1234', 'tidy stg_orders', '--- a\n+++ b\n')"
            )
        run_on(db, DAY)
        with connect(db.app_url, autocommit=True) as conn:
            results = run_monitors(
                conn, load_config(REPO / "monitors/datasets.yaml"), DAY, datetime.now(UTC)
            )
        yield db, results[0][1].incident_id


@pytest.fixture
def ctx(incident: tuple[ScratchDb, int]) -> Iterator[ToolContext]:
    db, incident_id = incident
    with (
        connect(db.app_url, autocommit=True) as app,
        connect(db.readonly_url, autocommit=True) as ro,
    ):
        yield ToolContext(app, ro, HashEmbedder(), incident_id)


def call(ctx: ToolContext, name: str, **args: Any) -> Any:
    result = execute(name, args, ctx)
    assert result.ok, result.error
    assert json.loads(result.content)["ok"] is True  # the LLM-facing text is valid JSON here
    return result.data


def test_get_incident(ctx: ToolContext) -> None:
    data = call(ctx, "get_incident")
    assert data["incident"]["root_candidate"] == "stg.order_items"
    first = data["alerts"][0]
    assert (first["detector"], first["error_class"]) == ("task_failure", "UndefinedColumn")
    assert data["logical_dates"] == [DAY]


def test_get_task_runs_and_logs(ctx: ToolContext) -> None:
    runs = call(ctx, "get_task_runs", task_id="stg_order_items")["runs"]
    failed = [r for r in runs if r["logical_date"] == DAY]
    assert [r["status"] for r in failed] == ["failed", "up_for_retry"]
    assert all(r["status"] == "success" for r in runs if r["logical_date"] != DAY)

    logs = call(ctx, "get_task_logs", run_id=failed[0]["run_id"])
    assert logs["task_id"] == "stg_order_items"
    assert "column i.unit_price does not exist" in logs["error_lines"][0]["message"]
    only_errors = call(ctx, "get_task_logs", run_id=failed[0]["run_id"], level="ERROR")
    assert {line["level"] for line in only_errors["lines"]} == {"ERROR"}
    grep = call(ctx, "get_task_logs", run_id=failed[0]["run_id"], grep="EXECUTING")
    assert grep["matched_lines"] == 1


def test_log_sampling_keeps_head_and_tail(ctx: ToolContext) -> None:
    run_id = call(ctx, "get_task_runs", task_id="ingest_orders", limit=1)["runs"][0]["run_id"]
    logs = call(ctx, "get_task_logs", run_id=run_id, limit=2)
    assert logs["omitted"] == logs["matched_lines"] - 2
    assert logs["lines"][0]["message"].startswith("Starting attempt")
    assert logs["lines"][-1]["message"] == "Marking task as SUCCESS"


def test_lookup_errors_guide_the_model(ctx: ToolContext) -> None:
    unknown_task = call(ctx, "get_task_runs", task_id="stg_order_item")
    assert unknown_task["runs"] == [] and "stg_order_items" in unknown_task["known_task_ids"]
    bad_run = execute("get_task_logs", {"run_id": "nope"}, ctx)
    assert not bad_run.ok and "get_task_runs" in (bad_run.error or "")
    typo = execute("get_lineage", {"table": "raw.order_item"}, ctx)
    assert not typo.ok and "did you mean raw.order_items" in (typo.error or "")


def test_get_lineage(ctx: ToolContext) -> None:
    data = call(ctx, "get_lineage", table="stg.order_items", direction="upstream", depth=2)
    tables = {t["table"]: t for t in data["tables"]}
    assert tables["raw.order_items"]["written_by"] == ["ingest_orders"]
    assert {"from": "raw.order_items.unit_price", "to": "stg.order_items.line_amount"} in data[
        "column_edges"
    ]
    down = call(ctx, "get_lineage", table="raw.order_items", direction="downstream", depth=3)
    assert "mart.daily_gmv_by_region" in {t["table"] for t in down["tables"]}


def test_run_readonly_sql(ctx: ToolContext) -> None:
    data = call(
        ctx, "run_readonly_sql",
        sql="SELECT logical_date, count(*) AS n FROM raw.order_items GROUP BY 1 ORDER BY 1",
    )  # fmt: skip
    assert data["columns"] == ["logical_date", "n"]
    assert data["row_count"] == 9 and data["sql_executed"].endswith("LIMIT 200")
    capped = call(ctx, "run_readonly_sql", sql="SELECT order_id FROM raw.orders")
    assert capped["row_count"] == 200 and "row cap" in capped["note"]


def test_guard_rejections_are_reported_not_raised(ctx: ToolContext) -> None:
    result = execute("run_readonly_sql", {"sql": "SELECT * FROM ops.task_runs"}, ctx)
    assert not result.ok and result.blocked_reason == "schema_not_allowed"
    missing = execute("run_readonly_sql", {"sql": "SELECT unit_price FROM raw.order_items"}, ctx)
    assert not missing.ok and "UndefinedColumn" in (missing.error or "")  # the drift itself


def test_get_recent_deploys(ctx: ToolContext) -> None:
    deploys = call(ctx, "get_recent_deploys")["deploys"]
    assert [d["deploy_id"] for d in deploys] == ["d-7"]
    assert call(ctx, "get_recent_deploys", task_id="stg_order_items")["deploys"] == []


def test_search_knowledge(ctx: ToolContext) -> None:
    hits = call(ctx, "search_knowledge", query="UndefinedColumn column does not exist")["hits"]
    assert hits and all("#" in h["chunk_id"] for h in hits)
    assert any(h["chunk_id"].startswith("runbooks/schema_drift") for h in hits)
    only_pm = call(ctx, "search_knowledge", query="freshness", doc_type="postmortem", k=3)
    assert {h["doc_type"] for h in only_pm["hits"]} == {"postmortem"}


def test_submit_rca_validates_the_report(ctx: ToolContext) -> None:
    bad = execute("submit_rca", {"summary": "x", "category": "schema_drift"}, ctx)
    assert not bad.ok and bad.blocked_reason == "invalid_args"


# --------------------------------------------------------------------------- second layer


def test_readonly_role_blocks_what_a_guard_bug_would_let_through(ctx: ToolContext) -> None:
    ro = ctx.readonly_conn
    assert ro.execute("SHOW statement_timeout").fetchone() == {"statement_timeout": "5s"}
    for statement in (
        "DELETE FROM raw.orders",
        "INSERT INTO raw.products VALUES (999, 'x', 'x', 'x', 1, 2)",
        "SELECT * FROM ops.task_runs",
        "SELECT * FROM sim.faults",
        "SELECT * FROM kb.chunks",
        "CREATE TABLE raw.evil (id int)",
    ):
        with pytest.raises(psycopg.errors.InsufficientPrivilege):
            ro.execute(statement)
