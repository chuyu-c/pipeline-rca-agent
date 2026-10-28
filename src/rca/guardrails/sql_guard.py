"""AST-level guard for SQL written by the LLM (DESIGN §8, CLAUDE.md guardrail invariants 1-2).

`validate()` accepts exactly one read-only query over the warehouse schemas and returns a
normalised version with a clamped LIMIT. It is the first of two layers: the query must also run as
the `rca_readonly` role in a read-only transaction with a statement timeout.

Policy, checked on the parsed tree (never on the raw string):
- exactly one statement; its root is SELECT (incl. WITH ... SELECT) or a set operation of SELECTs
- no node anywhere that writes, locks, changes session state or is unparseable (a DELETE inside a
  CTE is still a DELETE)
- functions sqlglot doesn't recognise (`Anonymous`) are denied unless allowlisted — Postgres admin
  and side-effect functions (pg_sleep, dblink, set_config, nextval, lo_export, ...) all land there,
  so unknown variants are blocked by default; a denylist backs this up for known names
- every table is schema-qualified with a schema in {raw, stg, mart}; quoted identifiers are
  compared case-sensitively, as Postgres does
- LIMIT injected when missing, clamped when larger than the cap; FETCH FIRST is folded into it
- output is regenerated from the AST without comments
"""

from dataclasses import dataclass

import sqlglot
from sqlglot import exp
from sqlglot.errors import SqlglotError

ALLOWED_SCHEMAS = frozenset({"raw", "stg", "mart"})
_ALLOWED_LIST = " | ".join(sorted(ALLOWED_SCHEMAS))
DEFAULT_LIMIT = 200
MAX_SQL_CHARS = 5_000

# Harmless Postgres functions that sqlglot parses as Anonymous.
ALLOWED_ANONYMOUS_FUNCTIONS = frozenset(
    {
        "age", "every", "make_date", "date_part", "to_char", "jsonb_agg", "jsonb_array_length",
        "jsonb_build_object", "jsonb_extract_path_text", "jsonb_typeof", "json_extract_path_text",
    }
)  # fmt: skip
# Never allowed, whatever sqlglot parses them as.
DENIED_FUNCTIONS = frozenset(
    {
        "pg_sleep", "pg_sleep_for", "pg_sleep_until", "pg_read_file", "pg_read_binary_file",
        "pg_ls_dir", "pg_stat_file", "pg_terminate_backend", "pg_cancel_backend",
        "pg_reload_conf", "pg_rotate_logfile", "lo_import", "lo_export", "dblink", "dblink_exec",
        "set_config", "current_setting", "nextval", "setval", "query_to_xml", "txid_current",
    }
)  # fmt: skip
_DENIED_PREFIXES = ("pg_", "lo_", "dblink")

# Node types that may not appear anywhere in the tree. Looked up by name so the guard keeps
# working across sqlglot versions that rename or drop a class.
_FORBIDDEN_NODE_NAMES = (
    "Insert", "Update", "Delete", "Merge", "Create", "Drop", "Alter", "AlterTable",
    "TruncateTable", "Copy", "Command", "Into", "Lock", "Set", "Transaction", "Commit",
    "Rollback", "Grant", "Revoke", "Use", "Pragma", "LoadData", "Analyze", "Comment",
)  # fmt: skip
_FORBIDDEN_NODES: tuple[type[exp.Expression], ...] = tuple(
    getattr(exp, name) for name in _FORBIDDEN_NODE_NAMES if hasattr(exp, name)
)


class SqlGuardError(ValueError):
    """The query was rejected. `reason` is a stable code; the message explains it to the LLM."""

    def __init__(self, reason: str, message: str) -> None:
        self.reason = reason
        super().__init__(message)


@dataclass(frozen=True)
class ValidatedSql:
    sql: str  # normalised, comment-free, with LIMIT applied
    tables: tuple[str, ...]  # schema.table referenced, sorted
    limit: int


def _identifier_name(ident: exp.Expression | str | None) -> str | None:
    """Effective Postgres name: unquoted identifiers fold to lower case, quoted ones don't."""
    if ident is None:
        return None
    if isinstance(ident, str):
        return ident.lower()
    if isinstance(ident, exp.Identifier):
        name = str(ident.this)
        return name if ident.quoted else name.lower()
    return ident.name.lower()


def _function_name(func: exp.Func) -> str:
    return (func.name if isinstance(func, exp.Anonymous) else func.sql_name()).lower()


def _check_functions(tree: exp.Expression) -> None:
    for func in tree.find_all(exp.Func):
        name = _function_name(func)
        if name in DENIED_FUNCTIONS or name.startswith(_DENIED_PREFIXES):
            raise SqlGuardError("denied_function", f"function {name}() is not allowed")
        if isinstance(func, exp.Anonymous) and name not in ALLOWED_ANONYMOUS_FUNCTIONS:
            raise SqlGuardError(
                "unknown_function",
                f"function {name}() is not on the allowlist; use standard SQL functions",
            )


def _cte_name(cte: exp.CTE) -> str | None:
    alias = cte.args.get("alias")
    return _identifier_name(alias.this) if isinstance(alias, exp.TableAlias) else None


def _check_tables(tree: exp.Expression) -> tuple[str, ...]:
    ctes = {_cte_name(cte) for cte in tree.find_all(exp.CTE)}
    tables: set[str] = set()
    for table in tree.find_all(exp.Table):
        if not isinstance(table.this, exp.Identifier):
            raise SqlGuardError("table_function", "functions in FROM are not allowed")
        name = _identifier_name(table.this)
        schema = _identifier_name(table.args.get("db"))
        if table.args.get("catalog") is not None:
            raise SqlGuardError("catalog", "use schema.table, without a database name")
        if schema is None:
            if name in ctes:
                continue
            raise SqlGuardError(
                "unqualified_table",
                f"table {name!r} must be schema-qualified ({_ALLOWED_LIST}.<table>)",
            )
        if schema not in ALLOWED_SCHEMAS:
            raise SqlGuardError(
                "schema_not_allowed",
                f"schema {schema!r} is not allowed; only {_ALLOWED_LIST}",
            )
        tables.add(f"{schema}.{name}")
    return tuple(sorted(tables))


def _int_literal(node: exp.Expression | None) -> int | None:
    if isinstance(node, exp.Literal) and node.is_int:
        return int(node.this)
    return None


def _apply_limit(query: exp.Query, cap: int) -> int:
    """Set the query's LIMIT to min(requested, cap). FETCH FIRST n is treated as LIMIT n
    (sqlglot stores it in the same slot); a non-literal, ALL or PERCENT request gets the cap."""
    requested: int | None = None
    limit = query.args.get("limit")
    if isinstance(limit, exp.Limit):
        requested = _int_literal(limit.expression)
    elif isinstance(limit, exp.Fetch):
        options = limit.args.get("limit_options")
        percent = isinstance(options, exp.Expression) and bool(options.args.get("percent"))
        requested = None if percent else _int_literal(limit.args.get("count"))
    effective = cap if requested is None else max(0, min(requested, cap))
    query.set("limit", exp.Limit(expression=exp.Literal.number(effective)))
    return effective


def validate(sql: str, *, max_rows: int = DEFAULT_LIMIT) -> ValidatedSql:
    if len(sql) > MAX_SQL_CHARS:
        raise SqlGuardError("too_long", f"query longer than {MAX_SQL_CHARS} characters")
    try:
        statements = [s for s in sqlglot.parse(sql, read="postgres") if s is not None]
    except SqlglotError as exc:
        raise SqlGuardError("parse_error", f"could not parse SQL: {exc}") from exc
    if len(statements) != 1:
        raise SqlGuardError(
            "multiple_statements" if statements else "empty",
            f"expected exactly one statement, got {len(statements)}",
        )
    tree = statements[0]

    if not isinstance(tree, exp.Select | exp.SetOperation):
        raise SqlGuardError(
            "not_select", f"only SELECT queries are allowed, got {type(tree).__name__.upper()}"
        )
    for node in tree.walk():
        if isinstance(node, _FORBIDDEN_NODES):
            raise SqlGuardError(
                "forbidden_clause", f"{type(node).__name__.upper()} is not allowed in a query"
            )
    for select in tree.find_all(exp.Select):
        if select is not tree and select.args.get("into") is not None:
            raise SqlGuardError("forbidden_clause", "SELECT INTO is not allowed")

    _check_functions(tree)
    tables = _check_tables(tree)
    assert isinstance(tree, exp.Query)
    limit = _apply_limit(tree, max_rows)
    return ValidatedSql(tree.sql(dialect="postgres", comments=False), tables, limit)
