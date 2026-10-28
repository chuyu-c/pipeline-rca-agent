"""Mini DAG runner: executes tasks in topological order, one transaction per attempt.

Scheduling (`run_dag`) is pure logic over two small protocols so it can be unit-tested without a
database; `PgExecutor` / `PgRunStore` are the Postgres implementations.
"""

import importlib
import time
import uuid
from collections.abc import Callable
from dataclasses import dataclass, field
from datetime import UTC, date, datetime
from enum import StrEnum
from typing import Any, Protocol

from psycopg import ClientCursor
from psycopg.types.json import Jsonb

from rca.db import Connection, connect
from rca.observability.logging import get_logger
from rca.simulator.conditions import load_fault
from rca.simulator.dag import Dag, LogFn, Task, TaskContext

log = get_logger(__name__)


class TaskStatus(StrEnum):
    SUCCESS = "success"
    UP_FOR_RETRY = "up_for_retry"
    FAILED = "failed"
    UPSTREAM_FAILED = "upstream_failed"


@dataclass
class TaskRun:
    """One attempt of one task (one `ops.task_runs` row)."""

    run_id: str
    dag_id: str
    task_id: str
    logical_date: date
    try_number: int
    status: TaskStatus
    started_at: datetime
    ended_at: datetime
    error_class: str | None = None
    error_message: str | None = None


@dataclass
class DagRunResult:
    dag_run_id: str
    attempts: list[TaskRun] = field(default_factory=list)

    @property
    def final(self) -> dict[str, TaskRun]:
        """Last attempt per task."""
        return {run.task_id: run for run in self.attempts}

    @property
    def ok(self) -> bool:
        return all(run.status is TaskStatus.SUCCESS for run in self.final.values())


class TaskExecutor(Protocol):
    def execute(self, dag: Dag, task: Task, logical_date: date, try_number: int, log: LogFn) -> int:
        """Run one attempt atomically; return rows affected. Raise on failure."""
        ...


class RunStore(Protocol):
    def record_run(self, run: TaskRun) -> None: ...

    def log(self, run_id: str, level: str, message: str, attrs: dict[str, Any]) -> None: ...


def _now() -> datetime:
    return datetime.now(UTC)


def run_dag(
    dag: Dag,
    logical_date: date,
    executor: TaskExecutor,
    store: RunStore,
    *,
    retry_delay_s: float = 0.0,
) -> DagRunResult:
    result = DagRunResult(dag_run_id=f"{logical_date:%Y%m%d}_{uuid.uuid4().hex[:8]}")
    final: dict[str, TaskStatus] = {}

    for task in dag.ordered_tasks():
        base_attrs: dict[str, Any] = {
            "dag_id": dag.dag_id,
            "task_id": task.id,
            "logical_date": logical_date.isoformat(),
            "dag_run_id": result.dag_run_id,
        }
        base_attrs.update({"sql_file": task.sql_file} if task.sql_file else {"python": task.python})

        blocked = {
            dep: final[dep] for dep in task.depends_on if final[dep] is not TaskStatus.SUCCESS
        }
        if blocked:
            run_id = f"{result.dag_run_id}.{task.id}.1"
            now = _now()
            states = ", ".join(f"{dep}={status}" for dep, status in sorted(blocked.items()))
            message = f"Not running: upstream task(s) not successful ({states})"
            store.log(run_id, "WARNING", message, {**base_attrs, "try_number": 1})
            run = TaskRun(
                run_id, dag.dag_id, task.id, logical_date, 1, TaskStatus.UPSTREAM_FAILED, now, now,
                error_class="UpstreamFailed", error_message=message,
            )  # fmt: skip
            store.record_run(run)
            result.attempts.append(run)
            final[task.id] = run.status
            log.warning("task skipped", extra={**base_attrs, "status": run.status})
            continue

        max_tries = task.retries + 1
        for try_number in range(1, max_tries + 1):
            run_id = f"{result.dag_run_id}.{task.id}.{try_number}"
            attrs = {**base_attrs, "try_number": try_number}

            def task_log(
                message: str,
                level: str = "INFO",
                *,
                _run_id: str = run_id,
                _attrs: dict[str, Any] = attrs,
            ) -> None:
                store.log(_run_id, level, message, _attrs)

            started = _now()
            task_log(f"Starting attempt {try_number} of {max_tries}")
            try:
                rows = executor.execute(dag, task, logical_date, try_number, task_log)
            except Exception as exc:
                error_class = type(exc).__name__
                error_message = (str(exc).strip().splitlines() or [""])[0]
                store.log(run_id, "ERROR", f"Task failed with {error_class}: {error_message}", {
                    **attrs, "error_class": error_class, "sqlstate": getattr(exc, "sqlstate", None),
                })  # fmt: skip
                status = TaskStatus.UP_FOR_RETRY if try_number < max_tries else TaskStatus.FAILED
                store.log(run_id, "INFO", f"Marking task as {status.upper()}", attrs)
                run = TaskRun(
                    run_id, dag.dag_id, task.id, logical_date, try_number, status, started, _now(),
                    error_class=error_class, error_message=error_message,
                )  # fmt: skip
            else:
                task_log(f"Task succeeded; {rows} rows written")
                task_log("Marking task as SUCCESS")
                run = TaskRun(
                    run_id, dag.dag_id, task.id, logical_date, try_number, TaskStatus.SUCCESS,
                    started, _now(),
                )  # fmt: skip

            store.record_run(run)
            result.attempts.append(run)
            log.info("task attempt finished", extra={**attrs, "status": run.status})
            if run.status is not TaskStatus.UP_FOR_RETRY:
                break
            if retry_delay_s:
                time.sleep(retry_delay_s)

        final[task.id] = result.attempts[-1].status

    return result


def format_run(logical_date: date, result: DagRunResult) -> str:
    lines = [f"{logical_date}  dag_run={result.dag_run_id}"]
    for task_id, run in result.final.items():
        error = f"  {run.error_class}: {run.error_message}" if run.error_class else ""
        lines.append(f"  {task_id:<28} {run.status:<16} try={run.try_number}{error}")
    return "\n".join(lines)


def run_pg(database_url: str, dag: Dag, logical_date: date, seed: int) -> DagRunResult:
    """Run `dag` against Postgres: one connection for task work, one for runs/logs."""
    with (
        connect(database_url, autocommit=True) as work_conn,
        connect(database_url, autocommit=True) as ops_conn,
    ):
        return run_dag(dag, logical_date, PgExecutor(work_conn, seed), PgRunStore(ops_conn))


def _resolve(spec: str) -> Callable[[TaskContext], int]:
    module_name, _, func_name = spec.partition(":")
    func: Callable[[TaskContext], int] = getattr(importlib.import_module(module_name), func_name)
    return func


class PgExecutor:
    """Runs each attempt in its own transaction on an autocommit connection, as `rca_app`.

    Honours injected task faults (`sim.faults`, target `task:<id>`):
      statement_timeout_ms   override the task's timeout
      transient_failures     fail the first N attempts with `transient_error` (a TimeoutError)
      sql, deploy_id, commit_sha   run a deployed SQL version instead of the repo file
    """

    def __init__(self, conn: Connection, seed: int) -> None:
        self.conn = conn
        self.seed = seed

    def execute(self, dag: Dag, task: Task, logical_date: date, try_number: int, log: LogFn) -> int:
        fault = load_fault(self.conn, f"task:{task.id}")
        timeout_ms = int(fault.get("statement_timeout_ms", task.timeout_s * 1000))
        with self.conn.transaction():
            self.conn.execute(f"SET LOCAL statement_timeout = {timeout_ms}")
            if task.python:
                log(f"Running python callable {task.python}")
            else:
                log(f"Executing {task.sql_file}" + (
                    f" (deploy {fault['deploy_id']}, commit {fault['commit_sha']})"
                    if "sql" in fault else ""
                ))  # fmt: skip
            if try_number <= int(fault.get("transient_failures", 0)):
                raise TimeoutError(
                    str(fault.get("transient_error", "operation timed out")).format(
                        logical_date=logical_date
                    )
                )

            if task.python:
                ctx = TaskContext(self.conn, logical_date, self.seed, try_number, log)
                return _resolve(task.python)(ctx)

            assert task.sql_file is not None
            sql = fault.get("sql") or (dag.root / task.sql_file).read_text(encoding="utf-8")
            # ClientCursor: multiple statements + parameters in one execute().
            with ClientCursor(self.conn) as cur:
                cur.execute(sql, {"logical_date": logical_date})
                rowcount = cur.rowcount
                while cur.nextset():
                    rowcount = cur.rowcount  # the last statement is the INSERT
            return rowcount


class PgRunStore:
    """Writes runs and logs on an autocommit connection so they survive task rollbacks."""

    def __init__(self, conn: Connection) -> None:
        self.conn = conn

    def record_run(self, run: TaskRun) -> None:
        self.conn.execute(
            "INSERT INTO ops.task_runs (run_id, dag_id, task_id, logical_date, try_number, status,"
            " started_at, ended_at, error_class, error_message)"
            " VALUES (%s, %s, %s, %s, %s, %s, %s, %s, %s, %s)",
            (
                run.run_id, run.dag_id, run.task_id, run.logical_date, run.try_number,
                str(run.status), run.started_at, run.ended_at, run.error_class, run.error_message,
            ),
        )  # fmt: skip

    def log(self, run_id: str, level: str, message: str, attrs: dict[str, Any]) -> None:
        self.conn.execute(
            "INSERT INTO ops.task_logs (run_id, ts, level, message, attrs)"
            " VALUES (%s, %s, %s, %s, %s)",
            (run_id, _now(), level, message, Jsonb(attrs)),
        )
