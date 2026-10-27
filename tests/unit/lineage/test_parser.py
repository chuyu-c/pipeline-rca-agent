import pytest

from rca.lineage.parser import Edge, LineageError, SchemaMap, extract_lineage


def tables(sql: str) -> set[tuple[str | None, str]]:
    return {
        (e.src_table, e.dst_table) for e in extract_lineage(sql, "t").edges if e.dst_column is None
    }


def columns(sql: str, schema: SchemaMap | None = None) -> set[tuple[str, str]]:
    return {
        (f"{e.src_table}.{e.src_column}", f"{e.dst_table}.{e.dst_column}")
        for e in extract_lineage(sql, "t", schema).edges
        if e.dst_column is not None
    }


def test_aliases_resolve_to_real_tables() -> None:
    sql = "INSERT INTO stg.a (x, y) SELECT o.id AS x, o.v * 2 AS y FROM raw.src AS o"
    assert tables(sql) == {("raw.src", "stg.a")}
    assert columns(sql) == {("raw.src.id", "stg.a.x"), ("raw.src.v", "stg.a.y")}


def test_target_columns_map_by_position_not_alias() -> None:
    sql = "INSERT INTO stg.a (first, second) SELECT s.p AS foo, s.q AS bar FROM raw.s AS s"
    assert columns(sql) == {("raw.s.p", "stg.a.first"), ("raw.s.q", "stg.a.second")}


def test_join_traces_each_column_to_its_side() -> None:
    sql = """
        INSERT INTO stg.items (order_id, sku, amount)
        SELECT i.order_id, p.sku, i.qty * p.price AS amount
        FROM raw.items AS i
        JOIN raw.products AS p ON p.id = i.product_id
    """
    assert tables(sql) == {("raw.items", "stg.items"), ("raw.products", "stg.items")}
    assert columns(sql) == {
        ("raw.items.order_id", "stg.items.order_id"),
        ("raw.products.sku", "stg.items.sku"),
        ("raw.items.qty", "stg.items.amount"),
        ("raw.products.price", "stg.items.amount"),
    }


def test_cte_is_not_a_table_and_columns_trace_through_it() -> None:
    sql = """
        INSERT INTO mart.m (region, total)
        WITH recent AS (
            SELECT o.region AS r, o.amount AS amt FROM raw.orders AS o
        ),
        agg AS (SELECT r, sum(amt) AS total FROM recent GROUP BY r)
        SELECT a.r, a.total FROM agg AS a
    """
    assert tables(sql) == {("raw.orders", "mart.m")}
    assert columns(sql) == {
        ("raw.orders.region", "mart.m.region"),
        ("raw.orders.amount", "mart.m.total"),
    }


def test_literals_and_count_star_have_no_column_source() -> None:
    sql = """
        INSERT INTO mart.m (d, n, src)
        SELECT o.d, count(*) AS n, 'web' AS src FROM raw.o AS o GROUP BY o.d
    """
    assert columns(sql) == {("raw.o.d", "mart.m.d")}
    assert tables(sql) == {("raw.o", "mart.m")}


def test_psycopg_params_and_non_insert_statements() -> None:
    sql = """
        DELETE FROM stg.a WHERE logical_date = %(logical_date)s;
        INSERT INTO stg.a (logical_date, v)
        SELECT s.logical_date, s.v FROM raw.s AS s
        WHERE s.logical_date = %(logical_date)s AND s.note NOT LIKE 'x%%';
    """
    result = extract_lineage(sql, "t")
    assert {(e.src_table, e.dst_table) for e in result.edges} == {("raw.s", "stg.a")}
    assert not result.warnings


def test_select_star_without_schema_warns_and_keeps_table_edge() -> None:
    sql = "INSERT INTO stg.copy SELECT * FROM raw.src"
    result = extract_lineage(sql, "copy_task")
    assert result.edges == [Edge("raw.src", None, "stg.copy", None, "copy_task")]
    assert len(result.warnings) == 1 and "SELECT *" in result.warnings[0]


def test_select_star_expands_with_schema() -> None:
    schema = {"raw": {"src": {"id": "int", "v": "text"}}}
    sql = "INSERT INTO stg.copy SELECT * FROM raw.src"
    assert columns(sql, schema=schema) == {
        ("raw.src.id", "stg.copy.id"),
        ("raw.src.v", "stg.copy.v"),
    }


def test_create_table_as_select() -> None:
    assert tables("CREATE TABLE mart.snap AS SELECT s.a FROM raw.s AS s") == {
        ("raw.s", "mart.snap")
    }


def test_unparseable_sql_raises_with_task_id() -> None:
    with pytest.raises(LineageError, match="'broken'"):
        extract_lineage("INSERT INTO (", "broken")
