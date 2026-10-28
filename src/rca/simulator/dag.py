"""DAG definitions (`dags/*.yaml`) and topological ordering."""

import heapq
from collections.abc import Iterable, Mapping
from dataclasses import dataclass
from datetime import date
from pathlib import Path
from typing import Protocol, Self

import yaml
from pydantic import BaseModel, Field, PrivateAttr, model_validator

from rca.db import Connection


class CycleError(ValueError):
    def __init__(self, cycle: list[str]) -> None:
        self.cycle = cycle
        super().__init__(f"dependency cycle: {' -> '.join(cycle)}")


def toposort(deps: Mapping[str, Iterable[str]]) -> list[str]:
    """Kahn's algorithm. `deps` maps node -> nodes it depends on.

    Ties are broken alphabetically so the order is deterministic.
    Raises ValueError for a dependency that isn't a node, CycleError if no order exists.
    """
    upstream = {node: set(ds) for node, ds in deps.items()}
    children: dict[str, list[str]] = {node: [] for node in upstream}
    for node, ds in upstream.items():
        for dep in ds:
            if dep not in upstream:
                raise ValueError(f"{node!r} depends on unknown node {dep!r}")
            children[dep].append(node)

    indegree = {node: len(ds) for node, ds in upstream.items()}
    ready = [node for node, n in indegree.items() if n == 0]
    heapq.heapify(ready)
    order: list[str] = []
    while ready:
        node = heapq.heappop(ready)
        order.append(node)
        for child in children[node]:
            indegree[child] -= 1
            if indegree[child] == 0:
                heapq.heappush(ready, child)

    if len(order) < len(upstream):
        blocked = {node for node, n in indegree.items() if n > 0}
        raise CycleError(_find_cycle(blocked, upstream))
    return order


def _find_cycle(blocked: set[str], upstream: Mapping[str, set[str]]) -> list[str]:
    """Walk upstream through blocked nodes until one repeats.

    Terminates because every blocked node has at least one blocked dependency.
    """
    path: list[str] = []
    seen: dict[str, int] = {}
    node = min(blocked)
    while node not in seen:
        seen[node] = len(path)
        path.append(node)
        node = min(upstream[node] & blocked)
    return [*path[seen[node] :], node]


class LogFn(Protocol):
    """Writes one line to the current attempt's task log (`ops.task_logs`)."""

    def __call__(self, message: str, level: str = "INFO") -> None: ...


def _no_log(message: str, level: str = "INFO") -> None:
    pass


@dataclass
class TaskContext:
    """What a `python:` task callable receives."""

    conn: Connection
    logical_date: date
    seed: int
    try_number: int = 1
    log: LogFn = _no_log


class Task(BaseModel):
    id: str
    sql_file: str | None = None
    python: str | None = Field(default=None, description="'module:function' taking a TaskContext")
    depends_on: list[str] = []
    # python tasks are opaque to lineage; declare the tables they load (e.g. raw.orders).
    produces: list[str] = []
    retries: int = Field(default=1, ge=0)
    timeout_s: int = Field(default=60, gt=0)

    @model_validator(mode="after")
    def _one_kind(self) -> Self:
        if (self.sql_file is None) == (self.python is None):
            raise ValueError(f"task {self.id!r}: set exactly one of sql_file / python")
        return self


class Dag(BaseModel):
    dag_id: str
    description: str = ""
    tasks: list[Task]
    _root: Path = PrivateAttr(default=Path("."))

    @model_validator(mode="after")
    def _valid_graph(self) -> Self:
        ids = [t.id for t in self.tasks]
        if len(ids) != len(set(ids)):
            raise ValueError(f"dag {self.dag_id!r}: duplicate task ids")
        toposort(self.dependencies())  # raises on unknown deps / cycles
        return self

    @property
    def root(self) -> Path:
        """Directory that `sql_file` paths are relative to."""
        return self._root

    def dependencies(self) -> dict[str, list[str]]:
        return {t.id: t.depends_on for t in self.tasks}

    def task(self, task_id: str) -> Task:
        return next(t for t in self.tasks if t.id == task_id)

    def ordered_tasks(self) -> list[Task]:
        return [self.task(tid) for tid in toposort(self.dependencies())]


def load_dag(path: Path) -> Dag:
    dag = Dag.model_validate(yaml.safe_load(path.read_text(encoding="utf-8")))
    dag._root = path.parent
    return dag
