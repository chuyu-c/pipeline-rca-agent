from pathlib import Path

import psycopg
import pytest

from rca.db import connect
from rca.lineage.build import build_all, load_edges, save_edges, warehouse_schema
from rca.lineage.graph import LineageGraph
from tests.integration.conftest import ScratchDb

pytestmark = pytest.mark.integration

DAGS_DIR = Path(__file__).parents[2] / "dags"


def test_build_save_load_roundtrip(test_db: ScratchDb) -> None:
    with connect(test_db.app_url, autocommit=True) as conn:
        schema = warehouse_schema(conn)
        assert "unit_price" in schema["raw"]["order_items"]

        results = build_all(DAGS_DIR, schema)
        edges = [e for r in results for e in r.edges]
        save_edges(conn, edges)
        save_edges(conn, edges)  # rebuild replaces, never duplicates
        loaded = load_edges(conn)

    assert sorted(loaded, key=lambda e: e.sort_key()) == sorted(edges, key=lambda e: e.sort_key())
    graph = LineageGraph(loaded)
    assert graph.topo_order(["mart.daily_gmv_by_region", "raw.orders"])[0] == "raw.orders"


def test_readonly_role_cannot_read_lineage(test_db: ScratchDb) -> None:
    with (
        connect(test_db.readonly_url, autocommit=True) as ro,
        pytest.raises(psycopg.errors.InsufficientPrivilege),
    ):
        ro.execute("SELECT * FROM lineage.edges")
