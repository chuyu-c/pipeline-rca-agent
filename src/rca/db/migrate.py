"""Forward-only SQL migrations: numbered files in `rca/db/migrations/`, applied in order.

Must run as the admin role (migrations create roles and own every table).
"""

from importlib import resources

from rca.db import Connection
from rca.observability.logging import get_logger

log = get_logger(__name__)


def available_migrations() -> list[tuple[str, str]]:
    """(version, sql) pairs sorted by filename, e.g. ("0001_schemas_and_roles", "...")."""
    files = resources.files("rca.db") / "migrations"
    return sorted(
        (f.name.removesuffix(".sql"), f.read_text(encoding="utf-8"))
        for f in files.iterdir()
        if f.name.endswith(".sql")
    )


def apply_migrations(conn: Connection) -> list[str]:
    """Apply pending migrations, one transaction each. Returns the versions applied."""
    with conn.transaction():
        conn.execute(
            "CREATE TABLE IF NOT EXISTS public.schema_migrations ("
            " version text PRIMARY KEY, applied_at timestamptz NOT NULL DEFAULT now())"
        )
    applied = {
        row["version"] for row in conn.execute("SELECT version FROM public.schema_migrations")
    }

    newly_applied = []
    for version, sql in available_migrations():
        if version in applied:
            continue
        with conn.transaction():
            conn.execute(sql)
            conn.execute("INSERT INTO public.schema_migrations (version) VALUES (%s)", (version,))
        log.info("migration applied", extra={"version": version})
        newly_applied.append(version)
    return newly_applied
