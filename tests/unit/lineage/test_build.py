"""Lineage of the real ecommerce_daily DAG, hand-checked against its SQL."""

from pathlib import Path

import pytest

from rca.lineage.build import dag_lineage
from rca.lineage.graph import LineageGraph
from rca.simulator.dag import Dag, load_dag

DAG_PATH = Path(__file__).parents[3] / "dags" / "ecommerce_daily.yaml"


@pytest.fixture(scope="module")
def dag() -> Dag:
    return load_dag(DAG_PATH)


@pytest.fixture(scope="module")
def graph(dag: Dag) -> LineageGraph:
    results = dag_lineage(dag)
    assert not [w for r in results for w in r.warnings]
    return LineageGraph(e for r in results for e in r.edges)


def test_daily_gmv_by_region_column_lineage(graph: LineageGraph) -> None:
    sg = graph.upstream("mart.daily_gmv_by_region", 1)
    cols = {(f"{e.src_table}.{e.src_column}", e.dst_column) for e in sg.edges if e.dst_column}
    assert cols == {
        ("stg.orders.logical_date", "logical_date"),
        ("stg.orders.region", "region"),
        ("stg.orders.order_id", "order_count"),
        ("stg.order_items.quantity", "units"),
        ("stg.order_items.line_amount", "gmv"),
    }


def test_schema_drift_column_reaches_marts(graph: LineageGraph) -> None:
    # raw.order_items.unit_price -> stg.order_items.line_amount -> mart.daily_gmv_by_region.gmv
    assert any(
        (e.src_table, e.src_column, e.dst_table, e.dst_column)
        == ("raw.order_items", "unit_price", "stg.order_items", "line_amount")
        for e in graph.edges
    )
    assert graph.descendants("raw.order_items") == {
        "stg.order_items", "mart.daily_gmv_by_region", "mart.sku_sell_through",
        "mart.inventory_health",
    }  # fmt: skip


def test_most_upstream_table_by_topo_order(graph: LineageGraph) -> None:
    alerted = ["mart.daily_gmv_by_region", "stg.order_items", "mart.inventory_health"]
    assert graph.topo_order(alerted)[0] == "stg.order_items"
    assert graph.producers("raw.orders") == {"ingest_orders"}


def test_dag_dependencies_cover_sql_lineage(dag: Dag, graph: LineageGraph) -> None:
    """Every table a task reads must be produced by a task it (transitively) depends on.

    Catches a YAML `depends_on` that drifted from the SQL. Tables nobody produces (the static
    raw.products dimension) are exempt.
    """
    deps = dag.dependencies()

    def upstream_tasks(task_id: str) -> set[str]:
        seen: set[str] = set()
        stack = list(deps[task_id])
        while stack:
            tid = stack.pop()
            if tid not in seen:
                seen.add(tid)
                stack.extend(deps[tid])
        return seen

    for edge in graph.edges:
        if edge.src_table is None or edge.dst_column is not None:
            continue
        producers = {e.task_id for e in graph.edges if e.dst_table == edge.src_table}
        if producers:
            assert producers <= upstream_tasks(edge.task_id), (edge.task_id, edge.src_table)
