from datetime import date

import psycopg
import pytest

from rca.db import connect
from rca.db.migrate import apply_migrations
from rca.simulator.runner import DagRunResult, TaskStatus
from tests.integration.conftest import ScratchDb, run_on, subdag

pytestmark = pytest.mark.integration

D = date(2026, 3, 2)
TABLES = (
    "raw.orders", "raw.order_items", "raw.inventory_snapshots", "stg.orders", "stg.order_items",
    "mart.daily_gmv_by_region", "mart.sku_sell_through", "mart.inventory_health",
)  # fmt: skip


def run(db: ScratchDb, logical_date: date = D) -> DagRunResult:
    return run_on(db, logical_date)


def row_counts(db: ScratchDb) -> dict[str, int]:
    with connect(db.admin_url) as conn:
        return {t: conn.execute(f"SELECT count(*) AS n FROM {t}").fetchone()["n"] for t in TABLES}  # type: ignore[index]


def test_full_run_succeeds_and_is_idempotent(test_db: ScratchDb) -> None:
    result = run(test_db)

    assert result.ok, {t: (r.status, r.error_message) for t, r in result.final.items()}
    first = row_counts(test_db)
    assert all(n > 0 for n in first.values()), first

    with connect(test_db.admin_url) as conn:
        runs = conn.execute("SELECT run_id, status FROM ops.task_runs").fetchall()
        logged = conn.execute("SELECT DISTINCT run_id FROM ops.task_logs").fetchall()
        regions = conn.execute("SELECT DISTINCT region FROM mart.daily_gmv_by_region").fetchall()
    assert len(runs) == 7 and {r["status"] for r in runs} == {"success"}
    assert {r["run_id"] for r in logged} == {r["run_id"] for r in runs}
    assert len(regions) == 5

    assert run(test_db).ok
    assert row_counts(test_db) == first  # partition overwrite, not append


def test_schema_drift_fails_stg_and_skips_downstream(test_db: ScratchDb) -> None:
    assert run(test_db).ok
    # Upstream producer renames a column after the data has landed.
    with connect(test_db.admin_url, autocommit=True) as conn:
        conn.execute("ALTER TABLE raw.order_items RENAME COLUMN unit_price TO price")

    result = run_on(test_db, D, subdag(lambda t: t.python is None))

    statuses = {tid: run.status for tid, run in result.final.items()}
    assert statuses["stg_orders"] is TaskStatus.SUCCESS
    assert statuses["stg_order_items"] is TaskStatus.FAILED
    assert result.final["stg_order_items"].error_class == "UndefinedColumn"
    assert {statuses[t] for t in statuses if t.startswith("mart_")} == {TaskStatus.UPSTREAM_FAILED}

    with connect(test_db.admin_url) as conn:
        attempts = conn.execute(
            "SELECT try_number, status FROM ops.task_runs"
            " WHERE task_id = 'stg_order_items' AND run_id LIKE %s ORDER BY try_number",
            (f"{result.dag_run_id}.%",),
        ).fetchall()
        errors = conn.execute(
            "SELECT message FROM ops.task_logs WHERE level = 'ERROR' AND run_id LIKE %s",
            (f"{result.dag_run_id}.stg_order_items.%",),
        ).fetchall()
    assert [(a["try_number"], a["status"]) for a in attempts] == [
        (1, "up_for_retry"),
        (2, "failed"),
    ]
    assert any("unit_price" in e["message"] for e in errors), errors


def test_roles_enforce_least_privilege(test_db: ScratchDb) -> None:
    run(test_db)
    with connect(test_db.readonly_url, autocommit=True) as ro:
        assert ro.execute("SELECT count(*) AS n FROM mart.daily_gmv_by_region").fetchone()
        with pytest.raises(psycopg.errors.InsufficientPrivilege):
            ro.execute("INSERT INTO raw.products VALUES (999, 'x', 'x', 'x', 1, 2)")
        with pytest.raises(psycopg.errors.InsufficientPrivilege):
            ro.execute("SELECT * FROM ops.task_runs")
    with (
        connect(test_db.app_url, autocommit=True) as app,
        pytest.raises(psycopg.errors.InsufficientPrivilege),
    ):
        app.execute("CREATE TABLE raw.sneaky (id int)")


def test_migrations_are_idempotent(test_db: ScratchDb) -> None:
    with connect(test_db.admin_url, autocommit=True) as conn:
        assert apply_migrations(conn) == []
