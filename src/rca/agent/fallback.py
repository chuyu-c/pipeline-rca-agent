"""Deterministic fallback triage (DESIGN §5.2 step 4): used when the LLM fails or a budget runs
out, and as the eval baseline.

Deliberately simple, as specified: root = the incident's topological root candidate (the
most-upstream alerted table), category = a regex classifier over the failed tasks' errors and the
alert detectors. It does no multi-hop investigation — measuring how much the agent adds over this
is the point of the baseline, so it must not be tuned to the eval scenarios.
"""

import re
from dataclasses import dataclass, field
from datetime import date
from typing import Any

from rca.agent.schema import Action, Escalate, Evidence, QuarantinePartition, RCAReport
from rca.db import Connection
from rca.taxonomy import Category

# (category, pattern over "<error_class>: <error_message>") — first match wins.
ERROR_PATTERNS: tuple[tuple[Category, re.Pattern[str]], ...] = (
    (
        "schema_drift",
        re.compile(r"UndefinedColumn|column .* does not exist|DatatypeMismatch", re.I),
    ),
    ("permission", re.compile(r"InsufficientPrivilege|permission denied", re.I)),
    ("resource", re.compile(r"QueryCanceled|statement timeout|out of memory|DiskFull", re.I)),
)  # fmt: skip
# Without failures: the first detector present decides.
DETECTOR_CATEGORIES: tuple[tuple[str, Category], ...] = (
    ("null_rate", "data_quality"),
    ("freshness", "late_upstream"),
    ("volume", "duplicate_load"),
)


@dataclass
class IncidentFacts:
    incident_id: int
    root_candidate: str | None
    logical_date: date | None
    alerts: list[dict[str, Any]] = field(default_factory=list)
    failures: list[dict[str, Any]] = field(default_factory=list)  # latest failed attempt per task


def gather_facts(conn: Connection, incident_id: int) -> IncidentFacts:
    incident = conn.execute(
        "SELECT root_candidate FROM ops.incidents WHERE incident_id = %s", (incident_id,)
    ).fetchone()
    alerts = conn.execute(
        "SELECT detector, dataset, error_class, logical_date, payload FROM ops.alerts"
        " WHERE incident_id = %s ORDER BY alert_id",
        (incident_id,),
    ).fetchall()
    logical_date = max((a["logical_date"] for a in alerts), default=None)
    failures = conn.execute(
        "SELECT task_id, error_class, error_message FROM ("
        "  SELECT DISTINCT ON (task_id) * FROM ops.task_runs WHERE logical_date = %s"
        "  ORDER BY task_id, started_at DESC, try_number DESC"
        ") latest WHERE status = 'failed' ORDER BY started_at",
        (logical_date,),
    ).fetchall()
    return IncidentFacts(
        incident_id,
        incident["root_candidate"] if incident else None,
        logical_date,
        alerts,
        failures,
    )


def classify(facts: IncidentFacts) -> tuple[Category, str]:
    """(category, the text that decided it)."""
    for failure in facts.failures:
        text = f"{failure['error_class']}: {failure['error_message']}"
        for category, pattern in ERROR_PATTERNS:
            if pattern.search(text):
                return category, f"{failure['task_id']} failed with {text}"
    if facts.failures:
        f = facts.failures[0]
        return "unknown", f"{f['task_id']} failed with {f['error_class']}: {f['error_message']}"
    detectors = {a["detector"] for a in facts.alerts}
    for detector, category in DETECTOR_CATEGORIES:
        if detector in detectors:
            hit = next(a for a in facts.alerts if a["detector"] == detector)
            return category, f"{detector} alert on {hit['dataset']} ({hit['error_class']})"
    return "unknown", "no failed tasks and no data alerts besides retries"


_ESCALATE_TO: dict[str, str] = {
    "schema_drift": "source owner",
    "late_upstream": "source owner",
    "duplicate_load": "pipeline owner",
    "permission": "data platform",
    "resource": "data platform",
}


def _actions(category: Category, facts: IncidentFacts, root: str) -> list[Action]:
    why = "heuristic default for this category; verify before approving"
    if category == "data_quality" and facts.logical_date is not None:
        table = ".".join(root.split(".")[:2])
        quarantine = QuarantinePartition(
            type="quarantine_partition", table=table, logical_date=facts.logical_date, rationale=why
        )
        return [quarantine]
    if category in _ESCALATE_TO:
        message = f"Suspected {category} affecting {root}"
        team = _ESCALATE_TO[category]
        return [Escalate(type="escalate", team=team, message=message, rationale=why)]
    return []


def fallback_report(facts: IncidentFacts, reason: str) -> RCAReport:
    category, decided_by = classify(facts)
    root = facts.root_candidate or (facts.alerts[0]["dataset"] if facts.alerts else "unknown")
    if category == "data_quality":
        column = next(
            (
                a["error_class"].split(":", 1)[1]
                for a in facts.alerts
                if a["detector"] == "null_rate"
            ),
            None,
        )
        root = f"{root}.{column}" if column else root
    return RCAReport(
        summary=(
            f"Heuristic triage ({reason}). Category from error/alert pattern: {decided_by}. "
            f"Root entity is the most-upstream alerted dataset; not independently verified."
        ),
        category=category,
        root_entity=root,
        confidence=0.4 if category != "unknown" else 0.2,
        evidence=[Evidence(tool_call_id="fallback:incident", excerpt=decided_by[:500])],
        impacted_datasets=sorted({a["dataset"] for a in facts.alerts}),
        proposed_actions=_actions(category, facts, root),
        hypotheses_considered=["deterministic heuristic only (no investigation)"],
    )
