from datetime import date
from typing import Any

from rca.simulator.dag import Dag, Task
from rca.simulator.runner import LogFn, TaskRun, TaskStatus, run_dag

D = date(2026, 3, 2)


def make_dag() -> Dag:
    """ingest -> stg -> {mart_a, mart_b}; other is independent."""
    return Dag.model_validate(
        {"dag_id": "test", "tasks": [
            {"id": "ingest", "python": "x:y", "retries": 0},
            {"id": "stg", "sql_file": "stg.sql", "depends_on": ["ingest"], "retries": 0},
            {"id": "mart_a", "sql_file": "a.sql", "depends_on": ["stg"], "retries": 0},
            {"id": "mart_b", "sql_file": "b.sql", "depends_on": ["stg"], "retries": 0},
            {"id": "other", "sql_file": "o.sql", "retries": 0},
        ]}
    )  # fmt: skip


class UndefinedColumn(Exception):
    pass


class FakeExecutor:
    """Fails each task the configured number of times before succeeding."""

    def __init__(self, failures: dict[str, int] | None = None) -> None:
        self.failures = dict(failures or {})
        self.executed: list[str] = []

    def execute(self, dag: Dag, task: Task, logical_date: date, log: LogFn) -> int:
        self.executed.append(task.id)
        if self.failures.get(task.id, 0) > 0:
            self.failures[task.id] -= 1
            raise UndefinedColumn('column "unit_price" does not exist\nLINE 1: ...')
        return 10


class FakeStore:
    def __init__(self) -> None:
        self.runs: list[TaskRun] = []
        self.logs: list[tuple[str, str, str, dict[str, Any]]] = []

    def record_run(self, run: TaskRun) -> None:
        self.runs.append(run)

    def log(self, run_id: str, level: str, message: str, attrs: dict[str, Any]) -> None:
        self.logs.append((run_id, level, message, attrs))


def test_all_tasks_succeed_in_topological_order() -> None:
    executor, store = FakeExecutor(), FakeStore()
    result = run_dag(make_dag(), D, executor, store)

    assert result.ok
    assert executor.executed == ["ingest", "other", "stg", "mart_a", "mart_b"]
    assert [r.status for r in store.runs] == [TaskStatus.SUCCESS] * 5


def test_failure_marks_downstream_upstream_failed_and_does_not_run_it() -> None:
    dag = make_dag()
    executor, store = FakeExecutor({"stg": 99}), FakeStore()
    result = run_dag(dag, D, executor, store)

    statuses = {tid: run.status for tid, run in result.final.items()}
    assert statuses == {
        "ingest": TaskStatus.SUCCESS,
        "other": TaskStatus.SUCCESS,
        "stg": TaskStatus.FAILED,
        "mart_a": TaskStatus.UPSTREAM_FAILED,
        "mart_b": TaskStatus.UPSTREAM_FAILED,
    }
    assert "mart_a" not in executor.executed and "mart_b" not in executor.executed
    assert not result.ok

    failed = result.final["stg"]
    assert failed.error_class == "UndefinedColumn"
    assert failed.error_message == 'column "unit_price" does not exist'  # first line only
    assert any(
        level == "ERROR" and "UndefinedColumn" in msg and attrs["task_id"] == "stg"
        for _, level, msg, attrs in store.logs
    )


def test_upstream_failed_propagates_transitively() -> None:
    dag = make_dag()
    result = run_dag(dag, D, FakeExecutor({"ingest": 1}), FakeStore())
    assert result.final["stg"].status is TaskStatus.UPSTREAM_FAILED
    assert result.final["mart_a"].status is TaskStatus.UPSTREAM_FAILED


def test_flaky_task_succeeds_on_retry() -> None:
    dag = make_dag()
    dag.task("stg").retries = 2
    store = FakeStore()
    result = run_dag(dag, D, FakeExecutor({"stg": 1}), store)

    stg_runs = [r for r in store.runs if r.task_id == "stg"]
    assert [(r.try_number, r.status) for r in stg_runs] == [
        (1, TaskStatus.UP_FOR_RETRY),
        (2, TaskStatus.SUCCESS),
    ]
    assert stg_runs[0].run_id != stg_runs[1].run_id
    assert result.ok


def test_retries_exhausted_marks_failed() -> None:
    dag = make_dag()
    dag.task("stg").retries = 2
    executor, store = FakeExecutor({"stg": 99}), FakeStore()
    run_dag(dag, D, executor, store)

    stg_runs = [r for r in store.runs if r.task_id == "stg"]
    assert [r.status for r in stg_runs] == [
        TaskStatus.UP_FOR_RETRY,
        TaskStatus.UP_FOR_RETRY,
        TaskStatus.FAILED,
    ]
    assert executor.executed.count("stg") == 3
