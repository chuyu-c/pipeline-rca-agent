"""`make demo`: reset → inject → run the pipeline → run monitors → triage → print the RCA."""

from datetime import UTC, date, datetime

from rca.agent.service import LLMKind, format_outcome, make_llm, triage_incident
from rca.config import Settings
from rca.db import connect
from rca.faults.inject import inject, reset
from rca.faults.scenario import load_scenarios
from rca.monitor.config import load_config
from rca.monitor.store import format_alerts, format_incidents, run_monitors
from rca.simulator.dag import load_dag
from rca.simulator.runner import format_run, run_pg


def run_demo(
    settings: Settings,
    scenario_id: str,
    logical_date: date,
    dag_id: str,
    llm: LLMKind = "anthropic",
) -> int:
    scenarios = load_scenarios(settings.scenarios_dir)
    if scenario_id not in scenarios:
        print(f"unknown scenario {scenario_id!r}; choose from: {', '.join(scenarios)}")
        return 2
    scenario = scenarios[scenario_id]

    print(f"== {scenario.id}: {scenario.title}  (logical_date {logical_date})")
    with connect(settings.admin_database_url, autocommit=True) as admin:
        reset(admin, scenarios.values(), logical_date, settings.dags_dir)
        inject(admin, scenario, logical_date, settings.dags_dir)
    print("reset, then injected the fault\n\n-- pipeline run")

    dag = load_dag(settings.dags_dir / f"{dag_id}.yaml")
    print(
        format_run(
            logical_date, run_pg(settings.database_url, dag, logical_date, settings.sim_seed)
        )
    )

    with connect(settings.database_url, autocommit=True) as conn:
        if conn.execute("SELECT 1 FROM lineage.edges LIMIT 1").fetchone() is None:
            print("\nlineage.edges is empty — run `make seed` (or `make lineage`) first")
            return 1
        results = run_monitors(
            conn, load_config(settings.monitors_config), logical_date, datetime.now(UTC)
        )
        print(f"\n-- alerts ({len(results)})")
        print(format_alerts(results) or "  none")
        if results:
            print("\n-- incident(s)")
            print(format_incidents(conn, (r.incident_id for _, r in results)))

    if not results:
        return 1
    incident_id = results[0][1].incident_id
    print(f"\n-- triage incident #{incident_id} (llm={llm})")
    outcome = triage_incident(settings, incident_id, make_llm(settings, llm))
    print(format_outcome(outcome))

    truth = scenario.ground_truth
    hit = (
        outcome.report.category == truth.category,
        outcome.report.root_entity == truth.root_entity,
    )
    print(f"\n-- ground truth: category={truth.category} root_entity={truth.root_entity}")
    print(f"   category {'✓' if hit[0] else '✗'}  root_entity {'✓' if hit[1] else '✗'}")
    return 0
