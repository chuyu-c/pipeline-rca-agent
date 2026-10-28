"""Python hooks for scenario steps. Signature: `hook(ctx: FaultContext, **args) -> None`."""

import difflib
from dataclasses import dataclass
from datetime import UTC, date, datetime, timedelta
from pathlib import Path
from typing import Any

from psycopg.types.json import Jsonb

from rca.db import Connection


@dataclass
class FaultContext:
    conn: Connection  # admin role, inside the inject/reset transaction
    logical_date: date
    scenario_dir: Path
    dags_dir: Path


def set_fault(ctx: FaultContext, target: str, params: dict[str, Any]) -> None:
    """Make a fake source ('source:orders') or task ('task:stg_orders') misbehave.

    Params merge into any already set for the target, so scenarios can compose faults.
    """
    ctx.conn.execute(
        "INSERT INTO sim.faults (target, params) VALUES (%s, %s)"
        " ON CONFLICT (target) DO UPDATE SET params = sim.faults.params || EXCLUDED.params",
        (target, Jsonb(params)),
    )


def deploy_sql(
    ctx: FaultContext,
    *,
    task_id: str,
    repo_sql_file: str,
    new_sql_file: str,
    deploy_id: str,
    commit_sha: str,
    author: str,
    message: str,
    minutes_before_run: int = 20,
) -> None:
    """Deploy a new SQL version of a task: record it in ops.deploys (with a real diff, as the
    agent will see it) and make the runner execute it instead of the repo file."""
    old = (ctx.dags_dir / repo_sql_file).read_text(encoding="utf-8")
    new = (ctx.scenario_dir / new_sql_file).read_text(encoding="utf-8")
    diff = "".join(
        difflib.unified_diff(
            old.splitlines(keepends=True),
            new.splitlines(keepends=True),
            fromfile=f"a/dags/{repo_sql_file}",
            tofile=f"b/dags/{repo_sql_file}",
        )
    )
    ts = datetime.now(UTC) - timedelta(minutes=minutes_before_run)
    ctx.conn.execute(
        "INSERT INTO ops.deploys (deploy_id, ts, author, task_id, commit_sha, message, diff)"
        " VALUES (%s, %s, %s, %s, %s, %s, %s)",
        (deploy_id, ts, author, task_id, commit_sha, message, diff),
    )
    set_fault(
        ctx, f"task:{task_id}", {"sql": new, "deploy_id": deploy_id, "commit_sha": commit_sha}
    )


def remove_deploy(ctx: FaultContext, *, deploy_id: str) -> None:
    ctx.conn.execute("DELETE FROM ops.deploys WHERE deploy_id = %s", (deploy_id,))
