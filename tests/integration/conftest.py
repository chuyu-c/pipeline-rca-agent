"""Integration fixtures: each test gets a throwaway, migrated database so the dev DB is untouched.

Needs `make up` (Postgres reachable at RCA_ADMIN_DATABASE_URL).
"""

import uuid
from collections.abc import Iterator
from dataclasses import dataclass

import pytest
from psycopg import sql
from psycopg.conninfo import make_conninfo

from rca.config import get_settings
from rca.db import connect
from rca.db.migrate import apply_migrations
from rca.simulator.ingest import load_products

SEED = 42


@dataclass(frozen=True)
class ScratchDb:
    admin_url: str
    app_url: str
    readonly_url: str


@pytest.fixture
def test_db() -> Iterator[ScratchDb]:
    settings = get_settings()
    name = f"rca_test_{uuid.uuid4().hex[:8]}"
    with connect(settings.admin_database_url, autocommit=True) as admin:
        admin.execute(sql.SQL("CREATE DATABASE {}").format(sql.Identifier(name)))
    db = ScratchDb(
        admin_url=make_conninfo(settings.admin_database_url, dbname=name),
        app_url=make_conninfo(settings.database_url, dbname=name),
        readonly_url=make_conninfo(settings.readonly_database_url, dbname=name),
    )
    try:
        with connect(db.admin_url, autocommit=True) as conn:
            apply_migrations(conn)
        with connect(db.app_url, autocommit=True) as conn:
            load_products(conn, SEED)
        yield db
    finally:
        with connect(settings.admin_database_url, autocommit=True) as admin:
            admin.execute(
                sql.SQL("DROP DATABASE IF EXISTS {} WITH (FORCE)").format(sql.Identifier(name))
            )
