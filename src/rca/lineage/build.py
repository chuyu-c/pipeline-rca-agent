"""Build lineage for whole DAGs and persist it to `lineage.edges`."""

from pathlib import Path

from rca.db import Connection
from rca.lineage.parser import Edge, SchemaMap, TaskLineage, extract_lineage
from rca.simulator.dag import Dag, load_dag


def dag_lineage(dag: Dag, schema: SchemaMap | None = None) -> list[TaskLineage]:
    results = []
    for task in dag.ordered_tasks():
        if task.sql_file:
            sql = (dag.root / task.sql_file).read_text(encoding="utf-8")
            results.append(extract_lineage(sql, task.id, schema))
        else:
            # Python tasks are opaque; they declare the tables they load from outside.
            edges = [Edge(None, None, table, None, task.id) for table in task.produces]
            warnings = [] if edges else [f"task {task.id!r}: python task declares no `produces`"]
            results.append(TaskLineage(task.id, edges, warnings))
    return results


def build_all(dags_dir: Path, schema: SchemaMap | None = None) -> list[TaskLineage]:
    return [
        r for path in sorted(dags_dir.glob("*.yaml")) for r in dag_lineage(load_dag(path), schema)
    ]


def warehouse_schema(conn: Connection) -> dict[str, dict[str, dict[str, str]]]:
    """Live column types for raw/stg/mart, used to expand `SELECT *`."""
    schema: dict[str, dict[str, dict[str, str]]] = {}
    rows = conn.execute(
        "SELECT table_schema, table_name, column_name, data_type FROM information_schema.columns"
        " WHERE table_schema IN ('raw', 'stg', 'mart') ORDER BY table_schema, table_name,"
        " ordinal_position"
    )
    for r in rows:
        tables = schema.setdefault(r["table_schema"], {})
        tables.setdefault(r["table_name"], {})[r["column_name"]] = r["data_type"]
    return schema


def save_edges(conn: Connection, edges: list[Edge]) -> None:
    """Replace the whole lineage graph atomically (it is always rebuilt from all DAGs)."""
    with conn.transaction(), conn.cursor() as cur:
        cur.execute("DELETE FROM lineage.edges")
        cur.executemany(
            "INSERT INTO lineage.edges (src_table, src_column, dst_table, dst_column, task_id)"
            " VALUES (%s, %s, %s, %s, %s)",
            [(e.src_table, e.src_column, e.dst_table, e.dst_column, e.task_id) for e in edges],
        )


def load_edges(conn: Connection) -> list[Edge]:
    rows = conn.execute(
        "SELECT src_table, src_column, dst_table, dst_column, task_id FROM lineage.edges"
    )
    return [Edge(**r) for r in rows]
