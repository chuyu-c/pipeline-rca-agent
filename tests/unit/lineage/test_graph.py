import pytest

from rca.lineage.graph import LineageGraph
from rca.lineage.parser import Edge


def t(src: str | None, dst: str, task: str = "task") -> Edge:
    return Edge(src, None, dst, None, task)


@pytest.fixture
def graph() -> LineageGraph:
    """
    (ext) -> raw.a -> stg.a -> mart.x
             raw.b -> stg.b -> mart.x
                      stg.b -> mart.y
    """
    return LineageGraph([
        t(None, "raw.a", "ingest_a"), t(None, "raw.b", "ingest_b"),
        t("raw.a", "stg.a", "stg_a"), t("raw.b", "stg.b", "stg_b"),
        t("stg.a", "mart.x", "mart_x"), t("stg.b", "mart.x", "mart_x"),
        t("stg.b", "mart.y", "mart_y"),
        Edge("stg.a", "amount", "mart.x", "gmv", "mart_x"),
        Edge("mart.y", "v", "mart.y", "v", "mart_y"),  # incremental self-read
    ])  # fmt: skip


def test_upstream_respects_depth(graph: LineageGraph) -> None:
    assert graph.upstream("mart.x", 1).hops == {"mart.x": 0, "stg.a": 1, "stg.b": 1}
    assert graph.upstream("mart.x", 2).hops == {
        "mart.x": 0, "stg.a": 1, "stg.b": 1, "raw.a": 2, "raw.b": 2,
    }  # fmt: skip


def test_upstream_edges_include_columns_and_stay_inside_subgraph(graph: LineageGraph) -> None:
    edges = graph.upstream("mart.x", 1).edges
    assert Edge("stg.a", "amount", "mart.x", "gmv", "mart_x") in edges
    assert all(e.src_table in {"stg.a", "stg.b"} and e.dst_table == "mart.x" for e in edges)
    # At full depth the external loads of the raw tables are included.
    assert t(None, "raw.a", "ingest_a") in graph.upstream("mart.x", 3).edges


def test_downstream(graph: LineageGraph) -> None:
    assert graph.downstream("raw.b", 1).hops == {"raw.b": 0, "stg.b": 1}
    sg = graph.downstream("raw.b", 3)
    assert set(sg.hops) == {"raw.b", "stg.b", "mart.x", "mart.y"}
    assert all(e.src_table is not None for e in sg.edges)


def test_ancestors_and_descendants(graph: LineageGraph) -> None:
    assert graph.ancestors("mart.x") == {"stg.a", "stg.b", "raw.a", "raw.b"}
    assert graph.ancestors("raw.a") == set()
    assert graph.descendants("raw.b") == {"stg.b", "mart.x", "mart.y"}
    assert graph.ancestors("mart.y") == {"stg.b", "raw.b"}  # self-read is not a dependency


def test_topo_order(graph: LineageGraph) -> None:
    order = graph.topo_order()
    assert set(order) == graph.tables
    for e in graph.edges:
        if e.src_table and e.src_table != e.dst_table:
            assert order.index(e.src_table) < order.index(e.dst_table)
    assert graph.topo_order(["mart.y", "raw.b", "stg.b"]) == ["raw.b", "stg.b", "mart.y"]


def test_producers(graph: LineageGraph) -> None:
    assert graph.producers("mart.x") == {"mart_x"}
    assert graph.producers("raw.a") == {"ingest_a"}


def test_unknown_table_and_bad_depth(graph: LineageGraph) -> None:
    with pytest.raises(KeyError):
        graph.upstream("raw.nope")
    with pytest.raises(KeyError):
        graph.topo_order(["raw.nope"])
    with pytest.raises(ValueError, match="depth"):
        graph.downstream("raw.a", 0)
