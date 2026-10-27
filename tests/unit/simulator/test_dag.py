from pathlib import Path

import pytest
from pydantic import ValidationError

from rca.simulator.dag import CycleError, Dag, load_dag, toposort

REPO_DAGS = Path(__file__).parents[3] / "dags"


def test_toposort_linear() -> None:
    assert toposort({"c": ["b"], "b": ["a"], "a": []}) == ["a", "b", "c"]


def test_toposort_diamond_respects_all_edges() -> None:
    deps = {"a": [], "b": ["a"], "c": ["a"], "d": ["b", "c"]}
    order = toposort(deps)
    for node, ups in deps.items():
        assert all(order.index(up) < order.index(node) for up in ups)


def test_toposort_ties_are_alphabetical() -> None:
    assert toposort({"z": [], "m": [], "a": [], "b": ["z"]}) == ["a", "m", "z", "b"]


def test_toposort_two_node_cycle() -> None:
    with pytest.raises(CycleError) as exc:
        toposort({"a": ["b"], "b": ["a"], "c": []})
    assert set(exc.value.cycle) == {"a", "b"}
    assert exc.value.cycle[0] == exc.value.cycle[-1]


def test_toposort_self_loop() -> None:
    with pytest.raises(CycleError) as exc:
        toposort({"a": ["a"]})
    assert exc.value.cycle == ["a", "a"]


def test_toposort_reports_cycle_not_nodes_blocked_by_it() -> None:
    # d is downstream of the b<->c cycle: blocked, but not part of the cycle.
    with pytest.raises(CycleError) as exc:
        toposort({"a": [], "b": ["a", "c"], "c": ["b"], "d": ["c"]})
    assert set(exc.value.cycle) == {"b", "c"}


def test_toposort_unknown_dependency() -> None:
    with pytest.raises(ValueError, match="unknown node 'ghost'"):
        toposort({"a": ["ghost"]})


def test_dag_rejects_cycle_and_ambiguous_task() -> None:
    with pytest.raises(ValidationError, match="cycle"):
        Dag.model_validate(
            {"dag_id": "d", "tasks": [
                {"id": "a", "sql_file": "a.sql", "depends_on": ["b"]},
                {"id": "b", "sql_file": "b.sql", "depends_on": ["a"]},
            ]}
        )  # fmt: skip
    with pytest.raises(ValidationError, match="exactly one"):
        Dag.model_validate({"dag_id": "d", "tasks": [{"id": "a"}]})


def test_repo_dag_is_valid_and_sql_files_exist() -> None:
    dag = load_dag(REPO_DAGS / "ecommerce_daily.yaml")
    order = [t.id for t in dag.ordered_tasks()]
    assert order[:2] == ["ingest_inventory", "ingest_orders"]
    for task in dag.tasks:
        if task.sql_file:
            assert (dag.root / task.sql_file).is_file(), task.sql_file
