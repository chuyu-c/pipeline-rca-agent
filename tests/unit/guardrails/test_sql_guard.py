"""Adversarial tests for the LLM SQL guard (CLAUDE.md guardrail invariants 1 and 2)."""

import pytest
import sqlglot

from rca.guardrails.sql_guard import DEFAULT_LIMIT, MAX_SQL_CHARS, SqlGuardError, validate


def rejected(sql: str) -> str:
    with pytest.raises(SqlGuardError) as exc:
        validate(sql)
    return exc.value.reason


def assert_read_only(sql: str) -> None:
    """The guard's output must itself be a single SELECT with a LIMIT within the cap."""
    tree = sqlglot.parse(sql, read="postgres")
    assert len(tree) == 1
    assert type(tree[0]).__name__ in {"Select", "Union", "Intersect", "Except"}
    assert "DELETE" not in sql.upper() and "DROP" not in sql.upper().replace("'", "")


# --------------------------------------------------------------------------- allowed


@pytest.mark.parametrize(
    "sql",
    [
        "SELECT count(*) FROM raw.orders WHERE logical_date = DATE '2026-03-10'",
        "SELECT o.region, sum(i.line_amount) FROM stg.orders o JOIN stg.order_items i"
        " ON i.order_id = o.order_id GROUP BY 1",
        "WITH t AS (SELECT * FROM raw.order_items) SELECT count(*) FROM t",
        "SELECT * FROM mart.daily_gmv_by_region WHERE region IS NULL",
        "SELECT 1 AS a FROM raw.orders UNION ALL SELECT 2 FROM stg.orders",
        "SELECT * FROM raw.orders WHERE order_id IN (SELECT order_id FROM stg.orders)",
        "SELECT date_trunc('day', order_ts), coalesce(region, 'n/a') FROM raw.orders",
        "SELECT age(now(), min(order_ts)) FROM raw.orders",
        "SELECT _batch_id, count(*), count(DISTINCT order_id) FROM raw.orders GROUP BY 1",
        'SELECT * FROM "raw"."orders"',
        "SELECT * FROM raw.orders WHERE status LIKE 'compl%'",
        "SELECT count(*) FROM raw.orders;",  # one trailing semicolon is still one statement
    ],
)
def test_read_only_warehouse_queries_are_accepted(sql: str) -> None:
    result = validate(sql)
    assert_read_only(result.sql)
    assert result.limit <= DEFAULT_LIMIT
    assert set(result.tables) <= {
        "raw.orders", "raw.order_items", "stg.orders", "stg.order_items",
        "mart.daily_gmv_by_region",
    }  # fmt: skip


def test_tables_are_reported_and_ctes_are_not_tables() -> None:
    result = validate(
        "WITH t AS (SELECT * FROM raw.order_items) SELECT * FROM t JOIN raw.products p ON true"
    )
    assert result.tables == ("raw.order_items", "raw.products")


def test_string_literals_are_data_not_statements() -> None:
    result = validate("SELECT * FROM raw.orders WHERE status = 'x; DROP TABLE raw.orders; --'")
    assert "'x; DROP TABLE raw.orders; --'" in result.sql
    validate("SELECT $$ ; DELETE FROM raw.orders $$ AS s FROM raw.orders")


# --------------------------------------------------------------------------- LIMIT


@pytest.mark.parametrize(
    ("sql", "expected"),
    [
        ("SELECT * FROM raw.orders", DEFAULT_LIMIT),  # missing LIMIT -> injected
        ("SELECT * FROM raw.orders LIMIT 10", 10),
        ("SELECT * FROM raw.orders LIMIT 100000", DEFAULT_LIMIT),  # clamped
        ("SELECT * FROM raw.orders LIMIT ALL", DEFAULT_LIMIT),
        ("SELECT * FROM raw.orders LIMIT 1 + 100000", DEFAULT_LIMIT),  # non-literal
        ("SELECT * FROM raw.orders LIMIT 0", 0),
        ("SELECT * FROM raw.orders FETCH FIRST 5000 ROWS ONLY", DEFAULT_LIMIT),
        ("SELECT * FROM raw.orders FETCH FIRST 5 ROWS ONLY", 5),
        ("SELECT * FROM raw.orders FETCH FIRST 90 PERCENT ROWS ONLY", DEFAULT_LIMIT),
        ("SELECT * FROM raw.orders ORDER BY order_ts DESC LIMIT 50 OFFSET 10", 50),
        ("SELECT 1 FROM raw.orders UNION SELECT 2 FROM raw.order_items", DEFAULT_LIMIT),
    ],
)
def test_limit_is_injected_or_clamped(sql: str, expected: int) -> None:
    result = validate(sql)
    assert result.limit == expected
    assert f"LIMIT {expected}" in result.sql and result.sql.count("LIMIT") == 1
    assert "FETCH" not in result.sql and "PERCENT" not in result.sql


def test_inner_limits_do_not_satisfy_the_outer_cap() -> None:
    result = validate("SELECT * FROM (SELECT * FROM raw.orders LIMIT 5) s")
    assert result.sql.endswith(f"LIMIT {DEFAULT_LIMIT}")


def test_custom_row_cap() -> None:
    assert validate("SELECT * FROM raw.orders LIMIT 80", max_rows=20).limit == 20


# --------------------------------------------------------------------------- DML / DDL


@pytest.mark.parametrize(
    "sql",
    [
        "INSERT INTO raw.orders SELECT * FROM raw.orders",
        "UPDATE raw.orders SET region = NULL",
        "DELETE FROM raw.orders",
        "MERGE INTO raw.orders t USING stg.orders s ON t.order_id = s.order_id"
        " WHEN MATCHED THEN DELETE",
        "CREATE TABLE raw.x AS SELECT * FROM raw.orders",
        "DROP TABLE raw.orders",
        "ALTER TABLE raw.orders DROP COLUMN region",
        "TRUNCATE raw.orders",
        "GRANT ALL ON SCHEMA ops TO rca_readonly",
        "COPY raw.orders TO '/tmp/orders.csv'",
        "VACUUM raw.orders",
        "CALL refresh_everything()",
        "EXPLAIN ANALYZE DELETE FROM raw.orders",
        "SET statement_timeout = 0",
        "SET ROLE rca_admin",
        "BEGIN",
        "VALUES (1), (2)",
        "SHOW search_path",
    ],
)
def test_non_select_statements_are_rejected(sql: str) -> None:
    assert rejected(sql) in {"not_select", "forbidden_clause", "parse_error"}


# --------------------------------------------------------------------------- multiple statements


@pytest.mark.parametrize(
    "sql",
    [
        "SELECT 1 FROM raw.orders; DROP TABLE raw.orders",
        "SELECT 1 FROM raw.orders;DELETE FROM raw.orders;",
        "SELECT 1 FROM raw.orders; SELECT 2 FROM raw.orders",
        "SELECT 1 FROM raw.orders /* harmless */; UPDATE raw.orders SET region = NULL",
        "SELECT 1 FROM raw.orders; -- looks like a comment\nDELETE FROM raw.orders",
    ],
)
def test_multiple_statements_are_rejected(sql: str) -> None:
    assert rejected(sql) == "multiple_statements"


# --------------------------------------------------------------------------- comments


def test_comment_before_a_write_does_not_hide_it() -> None:
    assert rejected("/* just a read */ DELETE FROM raw.orders") == "not_select"
    assert rejected("-- SELECT 1\nDELETE FROM raw.orders") == "not_select"


def test_statement_inside_a_comment_is_dropped_from_the_output() -> None:
    result = validate("SELECT 1 FROM raw.orders /* ; DROP TABLE raw.orders */")
    assert "DROP" not in result.sql and "/*" not in result.sql


def test_line_comment_containing_a_block_terminator_cannot_smuggle_sql() -> None:
    # A naive re-emitter would turn this line comment into `/* */ DROP TABLE ... /* */`.
    result = validate("SELECT 1 FROM raw.orders -- */ DROP TABLE raw.orders; /*\n")
    assert "DROP" not in result.sql and "--" not in result.sql and "/*" not in result.sql
    assert_read_only(result.sql)


def test_only_comments_is_empty() -> None:
    assert rejected("-- nothing here") in {"empty", "parse_error"}


# --------------------------------------------------------------------------- CTE / nested writes


@pytest.mark.parametrize(
    "sql",
    [
        "WITH d AS (DELETE FROM raw.orders RETURNING *) SELECT * FROM d",
        "WITH u AS (UPDATE raw.orders SET region = NULL RETURNING *) SELECT count(*) FROM u",
        "WITH i AS (INSERT INTO raw.orders SELECT * FROM raw.orders RETURNING *) SELECT 1",
        "WITH a AS (SELECT 1), d AS (DELETE FROM raw.orders RETURNING *) SELECT * FROM a",
        "SELECT * FROM raw.orders WHERE order_id IN"
        " (WITH d AS (DELETE FROM raw.orders RETURNING order_id) SELECT order_id FROM d)",
        "SELECT * INTO raw.orders_copy FROM raw.orders",
        "SELECT * FROM raw.orders FOR UPDATE",
        "SELECT * FROM raw.orders FOR SHARE",
    ],
)
def test_writes_and_locks_nested_anywhere_are_rejected(sql: str) -> None:
    assert rejected(sql) == "forbidden_clause"


# --------------------------------------------------------------------------- functions


@pytest.mark.parametrize(
    "sql",
    [
        "SELECT pg_sleep(10)",
        "SELECT PG_SLEEP(10) FROM raw.orders",
        "SELECT pg_catalog.pg_sleep(10)",
        "SELECT pg_sleep_for('5 minutes')",
        "SELECT * FROM raw.orders WHERE pg_sleep(1) IS NULL",
        "SELECT * FROM raw.orders WHERE order_id IN"
        " (SELECT order_id FROM stg.orders WHERE pg_sleep(1) IS NULL)",
        "WITH s AS (SELECT pg_sleep(30)) SELECT * FROM raw.orders",
        "SELECT pg_read_file('/etc/passwd')",
        "SELECT pg_terminate_backend(123)",
        "SELECT lo_export(1, '/tmp/x')",
        "SELECT dblink('host=evil', 'DROP TABLE raw.orders')",
        "SELECT set_config('statement_timeout', '0', false)",
        "SELECT current_setting('is_superuser')",
        "SELECT nextval('ops.task_logs_id_seq')",
        "SELECT query_to_xml('DELETE FROM raw.orders', true, false, '')",
    ],
)
def test_dangerous_functions_are_rejected(sql: str) -> None:
    assert rejected(sql) in {"denied_function", "unknown_function"}


def test_unknown_functions_are_denied_by_default() -> None:
    assert rejected("SELECT some_extension_fn(region) FROM raw.orders") == "unknown_function"


# --------------------------------------------------------------------------- schemas


@pytest.mark.parametrize(
    "sql",
    [
        "SELECT * FROM ops.task_runs",
        "SELECT * FROM kb.chunks",
        "SELECT * FROM lineage.edges",
        "SELECT * FROM sim.faults",
        "SELECT * FROM public.schema_migrations",
        "SELECT * FROM pg_catalog.pg_roles",
        "SELECT * FROM information_schema.tables",
        "SELECT * FROM raw.orders o JOIN ops.incidents i ON true",
        "SELECT * FROM raw.orders WHERE EXISTS (SELECT 1 FROM ops.alerts)",
        "WITH t AS (SELECT * FROM sim.faults) SELECT * FROM t",
        "SELECT 1 FROM raw.orders UNION SELECT 1 FROM ops.jobs",
    ],
)
def test_schemas_outside_the_warehouse_are_rejected(sql: str) -> None:
    assert rejected(sql) == "schema_not_allowed"


def test_unqualified_tables_are_rejected() -> None:
    # Resolution would depend on search_path; never guess.
    assert rejected("SELECT * FROM orders") == "unqualified_table"
    assert rejected("SELECT * FROM pg_roles") == "unqualified_table"


def test_catalog_qualified_tables_are_rejected() -> None:
    assert rejected("SELECT * FROM rca.raw.orders") == "catalog"


def test_table_functions_in_from_are_rejected() -> None:
    assert rejected("SELECT * FROM generate_series(1, 1000000000)") in {
        "table_function", "unqualified_table",
    }  # fmt: skip
    assert rejected("SELECT * FROM pg_ls_dir('.')") in {"denied_function", "table_function"}


def test_cte_names_cannot_launder_a_real_table() -> None:
    # A CTE named like a table only covers *unqualified* references.
    assert (
        rejected("WITH task_runs AS (SELECT 1) SELECT * FROM ops.task_runs") == "schema_not_allowed"
    )


# --------------------------------------------------------------------------- quoted identifiers


def test_quoted_identifiers_are_case_sensitive_like_postgres() -> None:
    assert validate('SELECT * FROM "raw"."orders"').tables == ("raw.orders",)
    assert validate("SELECT * FROM RAW.ORDERS").tables == ("raw.orders",)  # unquoted folds
    assert rejected('SELECT * FROM "RAW".orders') == "schema_not_allowed"
    assert rejected('SELECT * FROM "Raw"."orders"') == "schema_not_allowed"
    assert rejected('SELECT * FROM "ops"."task_runs"') == "schema_not_allowed"


def test_quoted_dotted_name_is_one_unqualified_identifier() -> None:
    assert rejected('SELECT * FROM "raw.orders"') == "unqualified_table"


def test_quoted_names_survive_normalisation_without_injection() -> None:
    result = validate('SELECT * FROM raw."orders; DROP TABLE x"')
    assert '"orders; DROP TABLE x"' in result.sql
    assert len(sqlglot.parse(result.sql, read="postgres")) == 1


# --------------------------------------------------------------------------- input hygiene


def test_too_long_empty_and_garbage() -> None:
    assert rejected("SELECT 1 FROM raw.orders WHERE " + "1=1 AND " * MAX_SQL_CHARS) == "too_long"
    assert rejected("") in {"empty", "parse_error"}
    assert rejected("SELEC * FRM raw.orders") in {"parse_error", "not_select"}
