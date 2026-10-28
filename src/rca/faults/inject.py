"""Apply and undo fault scenarios. Both run as the admin role in a single transaction."""

import importlib
from collections.abc import Callable, Iterable
from datetime import date
from pathlib import Path
from typing import Any

from psycopg import ClientCursor, sql

from rca.db import Connection
from rca.faults.hooks import FaultContext
from rca.faults.scenario import Scenario, Step
from rca.observability.logging import get_logger

log = get_logger(__name__)


def _resolve(spec: str) -> Callable[..., Any]:
    module_name, _, func_name = spec.partition(":")
    func: Callable[..., Any] = getattr(importlib.import_module(module_name), func_name)
    return func


def _run_step(step: Step, ctx: FaultContext) -> None:
    if step.python:
        _resolve(step.python)(ctx, **step.args)
        return
    assert step.sql is not None
    if "%(" in step.sql:
        with ClientCursor(ctx.conn) as cur:
            cur.execute(step.sql, {"logical_date": ctx.logical_date})
    else:
        ctx.conn.execute(step.sql)


def inject(conn: Connection, scenario: Scenario, logical_date: date, dags_dir: Path) -> None:
    """Apply a scenario's setup steps atomically. Call `reset` first for a clean baseline."""
    ctx = FaultContext(conn, logical_date, scenario.dir, dags_dir)
    with conn.transaction():
        for step in scenario.setup:
            _run_step(step, ctx)
    log.info("scenario injected", extra={"scenario": scenario.id})


def _clear_day(conn: Connection, logical_date: date) -> None:
    tables = conn.execute(
        "SELECT table_schema, table_name FROM information_schema.columns"
        " WHERE table_schema IN ('raw', 'stg', 'mart') AND column_name = 'logical_date'"
        " ORDER BY 1, 2"
    ).fetchall()
    for t in tables:
        conn.execute(
            sql.SQL("DELETE FROM {} WHERE logical_date = %s").format(
                sql.Identifier(t["table_schema"], t["table_name"])
            ),
            (logical_date,),
        )
    conn.execute(
        "DELETE FROM ops.task_logs WHERE run_id IN"
        " (SELECT run_id FROM ops.task_runs WHERE logical_date = %s)",
        (logical_date,),
    )
    conn.execute("DELETE FROM ops.task_runs WHERE logical_date = %s", (logical_date,))


def reset(
    conn: Connection, scenarios: Iterable[Scenario], logical_date: date, dags_dir: Path
) -> None:
    """Return the environment to healthy, with `logical_date` not yet run.

    Runs every scenario's teardown (they are idempotent), clears injected source/task faults,
    resolves open incidents (cancelling their queued triage), and deletes the day's warehouse
    partitions and scheduler history — otherwise a failed rerun would leave yesterday's good
    partitions in place and hide the freshness symptoms the scenarios rely on.
    """
    with conn.transaction():
        for scenario in scenarios:
            ctx = FaultContext(conn, logical_date, scenario.dir, dags_dir)
            for step in scenario.teardown:
                _run_step(step, ctx)
        conn.execute("DELETE FROM sim.faults")
        _clear_day(conn, logical_date)
        conn.execute(
            "UPDATE ops.jobs SET status = 'cancelled', last_error = 'incident resolved by reset'"
            " WHERE status = 'queued'"
            "   AND incident_id IN (SELECT incident_id FROM ops.incidents"
            "                       WHERE status <> 'resolved')"
        )
        conn.execute(
            "UPDATE ops.incidents SET status = 'resolved', updated_at = now()"
            " WHERE status <> 'resolved'"
        )
    log.info("environment reset")
