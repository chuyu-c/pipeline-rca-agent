"""Detectors: turn one logical date's warehouse and scheduler state into alert candidates.

Each check is a pure `evaluate_*` function plus a thin query, so the decision logic is
unit-testable.
"""

import statistics
from dataclasses import dataclass, field
from datetime import date, timedelta
from typing import Any, Literal

from psycopg import sql

from rca.db import Connection
from rca.lineage.graph import LineageGraph
from rca.monitor.config import DatasetMonitors, MonitorConfig, VolumeCheck

Severity = Literal["critical", "high", "medium", "low"]


@dataclass(frozen=True)
class AlertCandidate:
    detector: str
    dataset: str
    error_class: str
    severity: Severity
    logical_date: date
    payload: dict[str, Any] = field(default_factory=dict, compare=False)


# --------------------------------------------------------------------------- pure evaluators


def evaluate_freshness(dataset: str, logical_date: date, row_count: int) -> AlertCandidate | None:
    if row_count > 0:
        return None
    return AlertCandidate(
        "freshness", dataset, "partition_missing", "high", logical_date,
        {"message": f"no rows in {dataset} for logical_date={logical_date}"},
    )  # fmt: skip


def evaluate_volume(
    dataset: str, logical_date: date, row_count: int, history: list[int], check: VolumeCheck
) -> AlertCandidate | None:
    """z-score of today's row count vs trailing partitions. Empty partitions are freshness's job."""
    if row_count == 0 or len(history) < check.min_history:
        return None
    mean = statistics.fmean(history)
    # Floor the spread so a perfectly stable table doesn't alert on a one-row change.
    std = max(statistics.pstdev(history), 0.01 * mean, 1.0)
    z = (row_count - mean) / std
    if abs(z) <= check.z_threshold:
        return None
    return AlertCandidate(
        "volume", dataset, "volume_high" if z > 0 else "volume_low", "medium", logical_date,
        {"row_count": row_count, "trailing_mean": round(mean, 1), "trailing_std": round(std, 1),
         "z_score": round(z, 2), "history_days": len(history)},
    )  # fmt: skip


def evaluate_null_rate(
    dataset: str, logical_date: date, column: str, nulls: int, total: int, threshold: float
) -> AlertCandidate | None:
    if total == 0:
        return None
    rate = nulls / total
    if rate <= threshold:
        return None
    return AlertCandidate(
        "null_rate", dataset, f"null_rate:{column}", "medium", logical_date,
        {"column": column, "null_rate": round(rate, 4), "threshold": threshold,
         "nulls": nulls, "rows": total},
    )  # fmt: skip


# --------------------------------------------------------------------------- queries


def _table(dataset: str) -> sql.Identifier:
    schema, table = dataset.split(".")
    return sql.Identifier(schema, table)


def _count(conn: Connection, dataset: str, monitors: DatasetMonitors, logical_date: date) -> int:
    query = sql.SQL("SELECT count(*) AS n FROM {} WHERE {} = %s").format(
        _table(dataset), sql.Identifier(monitors.partition_column)
    )
    row = conn.execute(query, (logical_date,)).fetchone()
    return int(row["n"]) if row else 0


def _history(
    conn: Connection, dataset: str, monitors: DatasetMonitors, logical_date: date, days: int
) -> list[int]:
    """Row counts of the partitions in the `days` before `logical_date` that exist."""
    col = sql.Identifier(monitors.partition_column)
    query = sql.SQL(
        "SELECT {col} AS d, count(*) AS n FROM {table} WHERE {col} >= %s AND {col} < %s GROUP BY 1"
    ).format(col=col, table=_table(dataset))
    rows = conn.execute(query, (logical_date - timedelta(days=days), logical_date))
    return [int(r["n"]) for r in rows]


def _nulls(
    conn: Connection, dataset: str, monitors: DatasetMonitors, column: str, logical_date: date
) -> tuple[int, int]:
    query = sql.SQL(
        "SELECT count(*) FILTER (WHERE {c} IS NULL) AS nulls, count(*) AS total"
        " FROM {table} WHERE {p} = %s"
    ).format(
        c=sql.Identifier(column), table=_table(dataset), p=sql.Identifier(monitors.partition_column)
    )
    row = conn.execute(query, (logical_date,)).fetchone()
    return (int(row["nulls"]), int(row["total"])) if row else (0, 0)


def detect_task_failures(
    conn: Connection, graph: LineageGraph, logical_date: date
) -> list[AlertCandidate]:
    """Tasks whose latest attempt for the date failed. `upstream_failed` tasks don't alert: their
    missing partitions raise freshness alerts, which correlate to the same incident."""
    rows = conn.execute(
        "SELECT * FROM ("
        "  SELECT DISTINCT ON (task_id) task_id, run_id, dag_id, status, error_class, error_message"
        "  FROM ops.task_runs WHERE logical_date = %s"
        "  ORDER BY task_id, started_at DESC, try_number DESC"
        ") latest WHERE status = 'failed' ORDER BY task_id",
        (logical_date,),
    )
    alerts = []
    for r in rows:
        outputs = graph.outputs(r["task_id"])
        alerts.append(
            AlertCandidate(
                "task_failure",
                outputs[0] if outputs else f"task:{r['task_id']}",
                r["error_class"] or "UnknownError",
                "critical",
                logical_date,
                {
                    "task_id": r["task_id"],
                    "dag_id": r["dag_id"],
                    "run_id": r["run_id"],
                    "error_message": r["error_message"],
                    "outputs": outputs,
                },
            )
        )
    return alerts


def detect_task_retries(
    conn: Connection, graph: LineageGraph, logical_date: date
) -> list[AlertCandidate]:
    """Tasks that succeeded only after retrying (low severity: often transient, sometimes the
    first sign of a degrading source). Error class is that of the last failed attempt."""
    rows = conn.execute(
        "SELECT latest.task_id, latest.run_id, latest.dag_id, latest.try_number,"
        "       failed.error_class, failed.error_message"
        " FROM ("
        "  SELECT DISTINCT ON (task_id) * FROM ops.task_runs WHERE logical_date = %s"
        "  ORDER BY task_id, started_at DESC, try_number DESC"
        " ) latest"
        " JOIN LATERAL ("
        "  SELECT error_class, error_message FROM ops.task_runs f"
        "  WHERE f.task_id = latest.task_id AND f.logical_date = latest.logical_date"
        "    AND f.status = 'up_for_retry'"
        "    AND split_part(f.run_id, '.', 1) = split_part(latest.run_id, '.', 1)"
        "  ORDER BY f.try_number DESC LIMIT 1"
        " ) failed ON true"
        " WHERE latest.status = 'success' AND latest.try_number > 1 ORDER BY latest.task_id",
        (logical_date,),
    )
    alerts = []
    for r in rows:
        outputs = graph.outputs(r["task_id"])
        alerts.append(
            AlertCandidate(
                "task_retry",
                outputs[0] if outputs else f"task:{r['task_id']}",
                r["error_class"] or "UnknownError",
                "low",
                logical_date,
                {
                    "task_id": r["task_id"],
                    "dag_id": r["dag_id"],
                    "run_id": r["run_id"],
                    "attempts": r["try_number"],
                    "error_message": r["error_message"],
                    "outputs": outputs,
                },
            )
        )
    return alerts


def run_detectors(
    conn: Connection, config: MonitorConfig, graph: LineageGraph, logical_date: date
) -> list[AlertCandidate]:
    alerts = detect_task_failures(conn, graph, logical_date)
    alerts += detect_task_retries(conn, graph, logical_date)
    for dataset, monitors in config.datasets.items():
        row_count = _count(conn, dataset, monitors, logical_date)
        candidates: list[AlertCandidate | None] = []
        if monitors.freshness:
            candidates.append(evaluate_freshness(dataset, logical_date, row_count))
        if monitors.volume:
            history = _history(conn, dataset, monitors, logical_date, monitors.volume.lookback_days)
            candidates.append(
                evaluate_volume(dataset, logical_date, row_count, history, monitors.volume)
            )
        for column, threshold in monitors.null_rate.items():
            nulls, total = _nulls(conn, dataset, monitors, column, logical_date)
            candidates.append(
                evaluate_null_rate(dataset, logical_date, column, nulls, total, threshold)
            )
        alerts.extend(c for c in candidates if c is not None)
    return alerts
