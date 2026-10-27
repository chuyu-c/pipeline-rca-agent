"""Integration fixtures: each test gets a throwaway, migrated database so the dev DB is untouched.

Needs `make up` (Postgres reachable at RCA_ADMIN_DATABASE_URL).
"""

import uuid
from collections.abc import Callable, Iterator
from dataclasses import dataclass
from datetime import date
from pathlib import Path

import pytest
from psycopg import sql
from psycopg.conninfo import make_conninfo

from rca.config import get_settings
from rca.db import connect
from rca.db.migrate import apply_migrations
from rca.simulator.dag import Dag, Task, load_dag
from rca.simulator.ingest import load_products
from rca.simulator.runner import DagRunResult, PgExecutor, PgRunStore, run_dag

SEED = 42
DAG_PATH = Path(__file__).parents[2] / "dags" / "ecommerce_daily.yaml"


def subdag(keep: Callable[[Task], bool]) -> Dag:
    """The repo DAG restricted to tasks matching `keep`; dependencies on dropped tasks are removed.

    Used to replay part of a day, e.g. reprocess data that has already landed in raw.
    """
    full = load_dag(DAG_PATH)
    kept = {t.id for t in full.tasks if keep(t)}
    sub = Dag(
        dag_id=full.dag_id,
        tasks=[
            t.model_copy(update={"depends_on": [d for d in t.depends_on if d in kept]})
            for t in full.tasks
            if t.id in kept
        ],
    )
    sub._root = full.root
    return sub


def run_on(db: "ScratchDb", logical_date: date, dag: Dag | None = None) -> DagRunResult:
    with (
        connect(db.app_url, autocommit=True) as work,
        connect(db.app_url, autocommit=True) as ops,
    ):
        return run_dag(
            dag or load_dag(DAG_PATH), logical_date, PgExecutor(work, SEED), PgRunStore(ops)
        )


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
