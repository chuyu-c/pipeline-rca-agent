"""Extract table- and column-level lineage edges from task SQL with sqlglot (postgres dialect).

Only statements that write rows from a query produce edges: `INSERT … SELECT` and
`CREATE TABLE … AS SELECT`. Other statements (the partition `DELETE`, `SET`, …) are ignored.
"""

import re
from collections.abc import Mapping
from dataclasses import dataclass, field

import sqlglot
from sqlglot import exp
from sqlglot.errors import SqlglotError
from sqlglot.lineage import lineage

from rca.observability.logging import get_logger

log = get_logger(__name__)

# {schema: {table: {column: type}}}, the shape sqlglot expects. Only used to expand `SELECT *`.
SchemaMap = Mapping[str, Mapping[str, Mapping[str, str]]]

# psycopg named parameters, e.g. %(logical_date)s, and the escaped percent sign.
_PARAM = re.compile(r"%\((\w+)\)s")


class LineageError(ValueError):
    pass


@dataclass(frozen=True, order=True)
class Edge:
    """`src_column`/`dst_column` are None for a table-level edge.

    `src_table` is None when the table is loaded from outside the warehouse (e.g. an ingest task).
    """

    src_table: str | None
    src_column: str | None
    dst_table: str
    dst_column: str | None
    task_id: str

    def sort_key(self) -> tuple[str, ...]:
        return tuple(
            p or ""
            for p in (
                self.dst_table,
                self.dst_column,
                self.src_table,
                self.src_column,
                self.task_id,
            )
        )


@dataclass
class TaskLineage:
    task_id: str
    edges: list[Edge] = field(default_factory=list)
    warnings: list[str] = field(default_factory=list)


def _table_name(table: exp.Table) -> str:
    return ".".join(part for part in (table.db, table.name) if part)


def _strip_params(sql: str) -> str:
    """Replace psycopg placeholders with string literals so sqlglot can parse the SQL."""
    return _PARAM.sub(lambda m: f"'__{m.group(1)}__'", sql).replace("%%", "%")


def _source_tables(query: exp.Query) -> set[str]:
    ctes = {cte.alias_or_name for cte in query.find_all(exp.CTE)}
    return {
        _table_name(t)
        for t in query.find_all(exp.Table)
        if t.db or t.name not in ctes  # an unqualified name matching a CTE is the CTE
    }


def _target(stmt: exp.Expr) -> tuple[str, list[str], exp.Query] | None:
    """(destination table, explicit column list, source query) for row-writing statements."""
    if isinstance(stmt, exp.Insert | exp.Create) and isinstance(stmt.expression, exp.Query):
        dst = stmt.this
        columns: list[str] = []
        if isinstance(dst, exp.Schema):  # INSERT INTO t (a, b, …)
            columns = [c.name for c in dst.expressions]
            dst = dst.this
        if isinstance(dst, exp.Table):
            return _table_name(dst), columns, stmt.expression
    return None


def extract_lineage(sql: str, task_id: str, schema: SchemaMap | None = None) -> TaskLineage:
    result = TaskLineage(task_id)
    try:
        statements = sqlglot.parse(_strip_params(sql), dialect="postgres")
    except SqlglotError as exc:
        raise LineageError(f"task {task_id!r}: cannot parse SQL: {exc}") from exc

    edges: set[Edge] = set()
    for stmt in statements:
        target = _target(stmt) if stmt is not None else None
        if target is None:
            continue
        dst_table, dst_columns, query = target

        for src_table in _source_tables(query):
            edges.add(Edge(src_table, None, dst_table, None, task_id))

        # Column lineage. sqlglot keys outputs in select order, with `*` expanded when the
        # schema knows the table; INSERT target columns map to them by position.
        nodes = lineage(None, query, dict(schema or {}), dialect="postgres")
        assert isinstance(nodes, dict)
        outputs = list(nodes)
        if "*" in outputs:
            result.warnings.append(
                f"task {task_id!r}: SELECT * into {dst_table} with no schema to expand it — "
                "column lineage skipped; table-level edges kept"
            )
            continue
        if dst_columns and len(dst_columns) != len(outputs):
            result.warnings.append(
                f"task {task_id!r}: {len(dst_columns)} target columns but {len(outputs)} distinct "
                f"outputs for {dst_table} — column lineage skipped; table-level edges kept"
            )
            continue
        for dst_column, output in zip(dst_columns or outputs, outputs, strict=True):
            for leaf in nodes[output].walk():
                if leaf.downstream or not isinstance(leaf.source, exp.Table):
                    continue  # intermediate node, or a literal / count(*) with no source column
                src_column = leaf.name.rsplit(".", 1)[-1]
                src_table = _table_name(leaf.source)
                if src_column == "*":  # e.g. count(*): depends on rows, not a column
                    continue
                edges.add(Edge(src_table, src_column, dst_table, dst_column, task_id))

    result.edges = sorted(edges, key=Edge.sort_key)
    result.warnings = sorted(set(result.warnings))
    for warning in result.warnings:
        log.warning(warning, extra={"task_id": task_id})
    return result
