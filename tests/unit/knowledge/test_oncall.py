"""The real oncall/ docs are complete, well-formed and consistent with the scenarios and lineage."""

from pathlib import Path

from rca.faults.scenario import load_scenarios
from rca.knowledge.chunking import chunk_document, load_oncall
from rca.lineage.build import dag_lineage
from rca.lineage.graph import LineageGraph
from rca.simulator.dag import load_dag
from rca.taxonomy import CATEGORIES

REPO = Path(__file__).parents[3]
DOCS = {d.doc_id: d for d in load_oncall(REPO / "oncall")}
TABLES = LineageGraph(
    e for r in dag_lineage(load_dag(REPO / "dags" / "ecommerce_daily.yaml")) for e in r.edges
).tables


def test_one_runbook_per_category_plus_general() -> None:
    runbooks = {d.metadata["category"] for d in DOCS.values() if d.doc_type == "runbook"}
    assert runbooks == set(CATEGORIES)
    assert "runbooks/oncall_general" in DOCS


def test_postmortems_have_front_matter() -> None:
    postmortems = [d for d in DOCS.values() if d.doc_type == "postmortem"]
    assert len(postmortems) >= 10
    for doc in postmortems:
        assert doc.metadata["category"] in CATEGORIES, doc.doc_id
        assert doc.metadata["tables"] and set(doc.metadata["tables"]) <= TABLES, doc.doc_id
        assert doc.doc_id.removeprefix("postmortems/").startswith(doc.metadata["date"][:7])


def test_every_scenario_expected_doc_exists() -> None:
    for scenario in load_scenarios(REPO / "eval" / "scenarios").values():
        missing = set(scenario.ground_truth.expected_doc_ids) - set(DOCS)
        assert not missing, (scenario.id, missing)


def test_oncall_chunks_are_unique_and_reasonably_sized() -> None:
    chunks = [c for d in DOCS.values() for c in chunk_document(d)]
    assert len({c.chunk_id for c in chunks}) == len(chunks)
    assert all(40 < len(c.text) < 6000 for c in chunks), [
        (c.chunk_id, len(c.text)) for c in chunks if not 40 < len(c.text) < 6000
    ]
