from datetime import UTC, date, datetime, timedelta
from pathlib import Path

import pytest

from rca.db import connect
from rca.lineage.build import build_all, load_edges, save_edges, warehouse_schema
from rca.lineage.graph import LineageGraph
from rca.monitor.config import load_config
from rca.monitor.detectors import AlertCandidate, run_detectors
from rca.monitor.ingest import IngestResult
from rca.monitor.store import ingest_all
from tests.integration.conftest import DAG_PATH, ScratchDb, run_on, subdag

pytestmark = pytest.mark.integration

CONFIG = Path(__file__).parents[2] / "monitors" / "datasets.yaml"
START = date(2026, 3, 2)
HEALTHY_DAYS = 8
NOW = datetime(2026, 3, 11, 6, 0, tzinfo=UTC)


def monitor(
    db: ScratchDb, logical_date: date, now: datetime
) -> list[tuple[AlertCandidate, IngestResult]]:
    with connect(db.app_url, autocommit=True) as conn:
        graph = LineageGraph(load_edges(conn))
        candidates = run_detectors(conn, load_config(CONFIG), graph, logical_date)
        return ingest_all(conn, candidates, graph, now)


@pytest.fixture
def warehouse(test_db: ScratchDb) -> ScratchDb:
    """Scratch DB with lineage and HEALTHY_DAYS of clean partitions."""
    with connect(test_db.app_url, autocommit=True) as conn:
        results = build_all(DAG_PATH.parent, warehouse_schema(conn))
        save_edges(conn, [e for r in results for e in r.edges])
    for offset in range(HEALTHY_DAYS):
        assert run_on(test_db, START + timedelta(days=offset)).ok
    return test_db


def test_healthy_day_raises_no_alerts(warehouse: ScratchDb) -> None:
    last_healthy = START + timedelta(days=HEALTHY_DAYS - 1)
    assert monitor(warehouse, last_healthy, NOW) == []


def test_one_upstream_break_is_one_incident(warehouse: ScratchDb) -> None:
    day = START + timedelta(days=HEALTHY_DAYS)
    # Data lands, then the producer renames a column before staging runs.
    assert run_on(warehouse, day, subdag(lambda t: t.python is not None)).ok
    with connect(warehouse.admin_url, autocommit=True) as conn:
        conn.execute("ALTER TABLE raw.order_items RENAME COLUMN unit_price TO price")
    assert not run_on(warehouse, day, subdag(lambda t: t.python is None)).ok

    results = monitor(warehouse, day, NOW)
    alerts = {(c.detector, c.dataset, c.error_class) for c, _ in results}
    assert alerts == {
        ("task_failure", "stg.order_items", "UndefinedColumn"),
        ("freshness", "stg.order_items", "partition_missing"),
        ("freshness", "mart.daily_gmv_by_region", "partition_missing"),
        ("freshness", "mart.sku_sell_through", "partition_missing"),
        ("freshness", "mart.inventory_health", "partition_missing"),
    }
    assert len({r.incident_id for _, r in results}) == 1

    with connect(warehouse.admin_url) as conn:
        incidents = conn.execute("SELECT incident_id, root_candidate FROM ops.incidents").fetchall()
        jobs = conn.execute("SELECT kind, status, run_after FROM ops.jobs").fetchall()
    assert [i["root_candidate"] for i in incidents] == ["stg.order_items"]
    assert [(j["kind"], j["status"]) for j in jobs] == [("triage", "queued")]
    assert jobs[0]["run_after"] > NOW

    # The next monitor pass 5 minutes later re-detects everything: all deduped, nothing new.
    again = monitor(warehouse, day, NOW + timedelta(minutes=5))
    assert {r.action for _, r in again} == {"deduped"}
    with connect(warehouse.admin_url) as conn:
        counts = conn.execute("SELECT count(*) AS alerts, min(count) AS lo, max(count) AS hi"
                              " FROM ops.alerts").fetchone()  # fmt: skip
        n_incidents = conn.execute("SELECT count(*) AS n FROM ops.incidents").fetchone()
        n_jobs = conn.execute("SELECT count(*) AS n FROM ops.jobs").fetchone()
    assert counts == {"alerts": 5, "lo": 2, "hi": 2}
    assert n_incidents == {"n": 1} and n_jobs == {"n": 1}
