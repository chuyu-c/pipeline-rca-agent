"""Every scenario file is valid, complete and consistent with the DAG, lineage and monitors."""

import importlib
import re
from pathlib import Path

import pytest
from pydantic import ValidationError

from rca.faults.scenario import Scenario, Step, load_scenarios
from rca.lineage.build import dag_lineage
from rca.lineage.graph import LineageGraph
from rca.simulator.dag import load_dag

REPO = Path(__file__).parents[3]
SCENARIOS = load_scenarios(REPO / "eval" / "scenarios")
DAG = load_dag(REPO / "dags" / "ecommerce_daily.yaml")
GRAPH = LineageGraph(e for r in dag_lineage(DAG) for e in r.edges)
DESIGN_IDS = {
    "schema_drift", "late_upstream", "null_spike", "duplicate_load", "bad_deploy",
    "resource", "permission", "injection", "flaky_noise",
}  # fmt: skip
DETECTORS = {"task_failure", "task_retry", "freshness", "volume", "null_rate"}


def test_all_design_scenarios_exist() -> None:
    assert set(SCENARIOS) == DESIGN_IDS


@pytest.mark.parametrize("scenario", SCENARIOS.values(), ids=list(SCENARIOS))
def test_scenario_is_consistent(scenario: Scenario) -> None:
    for step in [*scenario.setup, *scenario.teardown]:
        if step.python:
            module, _, func = step.python.partition(":")
            assert callable(getattr(importlib.import_module(module), func)), step.python
        if step.args.get("new_sql_file"):
            assert (scenario.dir / step.args["new_sql_file"]).is_file()

    for expected in scenario.expected_alerts:
        assert expected.detector in DETECTORS
        assert expected.dataset in GRAPH.tables

    truth = scenario.ground_truth
    task_ids = {t.id for t in DAG.tasks}
    root = truth.root_entity
    assert (
        root in GRAPH.tables
        or root in task_ids
        or (root.rsplit(".", 1)[0] in GRAPH.tables)  # table.column
        or re.fullmatch(r"deploy:d-\d+", root)
    ), root
    assert set(truth.expected_impacted) <= GRAPH.tables
    assert all(
        re.fullmatch(r"(runbooks|postmortems)/[a-z0-9_-]+", d) for d in truth.expected_doc_ids
    )


def test_scenarios_that_mutate_schema_or_grants_have_a_teardown() -> None:
    for scenario in SCENARIOS.values():
        mutates = any(s.sql and re.match(r"\s*(ALTER|REVOKE)", s.sql) for s in scenario.setup)
        deploys = any(s.python and s.python.endswith(":deploy_sql") for s in scenario.setup)
        if mutates or deploys:
            assert scenario.teardown, scenario.id


def test_step_needs_exactly_one_kind() -> None:
    with pytest.raises(ValidationError):
        Step()
    with pytest.raises(ValidationError):
        Step(sql="SELECT 1", python="x:y")
    with pytest.raises(ValidationError):
        Step(sql="SELECT 1", args={"a": 1})
