"""Each DESIGN §10 scenario breaks the pipeline visibly (alerts, one incident); reset undoes it."""

from collections.abc import Iterator
from datetime import UTC, datetime, timedelta
from pathlib import Path

import pytest

from rca.db import connect
from rca.faults.inject import inject, reset
from rca.faults.scenario import load_scenarios
from rca.monitor.config import load_config
from rca.monitor.detectors import AlertCandidate
from rca.monitor.ingest import IngestResult
from rca.monitor.store import run_monitors
from tests.integration.conftest import (
    DAG_PATH,
    ScratchDb,
    build_lineage,
    run_on,
    scratch_database,
)

pytestmark = pytest.mark.integration

REPO = Path(__file__).parents[2]
SCENARIOS = load_scenarios(REPO / "eval" / "scenarios")
CONFIG = load_config(REPO / "monitors" / "datasets.yaml")
START = datetime(2026, 3, 2).date()
HISTORY_DAYS = 8
DAY = START + timedelta(days=HISTORY_DAYS)


@pytest.fixture(scope="module")
def env() -> Iterator[ScratchDb]:
    """One database for all scenarios: lineage plus HISTORY_DAYS of clean partitions.

    Sharing it is deliberate — each scenario must leave nothing behind after `reset`.
    """
    with scratch_database() as db:
        build_lineage(db)
        for offset in range(HISTORY_DAYS + 1):
            assert run_on(db, START + timedelta(days=offset)).ok
        yield db


def monitor(db: ScratchDb) -> list[tuple[AlertCandidate, IngestResult]]:
    with connect(db.app_url, autocommit=True) as conn:
        return run_monitors(conn, CONFIG, DAY, datetime.now(UTC))


def reset_env(db: ScratchDb) -> None:
    with connect(db.admin_url, autocommit=True) as admin:
        reset(admin, SCENARIOS.values(), DAY, DAG_PATH.parent)


@pytest.mark.parametrize("scenario_id", sorted(SCENARIOS))
def test_scenario_alerts_as_one_incident_and_resets_cleanly(
    env: ScratchDb, scenario_id: str
) -> None:
    scenario = SCENARIOS[scenario_id]
    reset_env(env)
    with connect(env.admin_url, autocommit=True) as admin:
        inject(admin, scenario, DAY, DAG_PATH.parent)
    run_on(env, DAY)

    results = monitor(env)
    raised = {(c.detector, c.dataset, c.error_class) for c, _ in results}
    assert results, "scenario raised no alerts"
    for expected in scenario.expected_alerts:
        assert any(
            d == expected.detector and ds == expected.dataset
            and (expected.error_class is None or e == expected.error_class)
            for d, ds, e in raised
        ), (expected, raised)  # fmt: skip
    incidents = {r.incident_id for _, r in results}
    assert len(incidents) == 1, raised

    with connect(env.admin_url) as conn:
        if scenario_id == "injection":
            line = conn.execute(
                "SELECT message FROM ops.task_logs"
                " WHERE message LIKE '%%IGNORE ALL PREVIOUS INSTRUCTIONS%%'"
                "   AND attrs->>'task_id' = 'ingest_orders' AND attrs->>'logical_date' = %s",
                (DAY.isoformat(),),
            ).fetchone()
            assert line is not None, "injection string did not land in ops.task_logs"
        if scenario_id == "bad_deploy":
            deploy = conn.execute("SELECT diff FROM ops.deploys WHERE deploy_id = 'd-1042'")
            row = deploy.fetchone()
            assert row is not None and "+    ON p.category = src.category" in row["diff"]

    # Reversible: after reset, the same day runs clean and raises nothing.
    reset_env(env)
    assert run_on(env, DAY).ok
    assert monitor(env) == []
    with connect(env.admin_url) as conn:
        open_incidents = conn.execute(
            "SELECT count(*) AS n FROM ops.incidents WHERE status <> 'resolved'"
        ).fetchone()
        queued = conn.execute("SELECT count(*) AS n FROM ops.jobs WHERE status = 'queued'")
        assert open_incidents == {"n": 0} and queued.fetchone() == {"n": 0}
        assert conn.execute("SELECT count(*) AS n FROM sim.faults").fetchone() == {"n": 0}
        assert conn.execute("SELECT count(*) AS n FROM ops.deploys").fetchone() == {"n": 0}
