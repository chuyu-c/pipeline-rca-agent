"""Alert ingest (DESIGN §5.1): dedup by fingerprint, correlate into incidents along lineage,
pick the root candidate, enqueue a debounced triage job.

The logic is DB-free over `AlertStore` and `Lineage` protocols; `rca.monitor.store` has the
Postgres store. Correlation rule: see docs/adr/0001-alert-correlation-by-lineage-path.md.
"""

import hashlib
from collections.abc import Iterable
from dataclasses import dataclass
from datetime import datetime, timedelta
from typing import Literal, Protocol

from rca.monitor.detectors import AlertCandidate

DEDUP_WINDOW = timedelta(minutes=30)
TRIAGE_DEBOUNCE = timedelta(seconds=60)


def fingerprint(detector: str, dataset: str, error_class: str) -> str:
    # Length-prefixed fields: ("a|b", "c") and ("a", "b|c") must not collide.
    material = "".join(f"{len(part)}:{part};" for part in (detector, dataset, error_class))
    return hashlib.sha256(material.encode()).hexdigest()


class Lineage(Protocol):
    @property
    def tables(self) -> set[str]: ...

    def ancestors(self, table: str) -> set[str]: ...

    def topo_order(self, tables: Iterable[str] | None = None) -> list[str]: ...


class AlertStore(Protocol):
    def find_recent_alert(self, fingerprint: str, since: datetime) -> tuple[int, int] | None:
        """(alert_id, incident_id) of the latest alert with this fingerprint seen at/after `since`
        whose incident is not resolved."""
        ...

    def bump_alert(self, alert_id: int, now: datetime) -> None: ...

    def open_incidents(self) -> list[tuple[int, set[str]]]:
        """(incident_id, alerted datasets) for unresolved incidents, oldest first."""
        ...

    def create_incident(self, now: datetime) -> int: ...

    def insert_alert(
        self, candidate: AlertCandidate, fingerprint: str, incident_id: int, now: datetime
    ) -> int: ...

    def set_root_candidate(self, incident_id: int, root: str, now: datetime) -> None: ...

    def enqueue_triage(self, incident_id: int, run_after: datetime) -> None:
        """Idempotent per incident; while the job is still queued, push `run_after` later."""
        ...


@dataclass(frozen=True)
class IngestResult:
    action: Literal["deduped", "attached", "opened"]
    alert_id: int
    incident_id: int
    root_candidate: str | None = None


def same_lineage_path(a: str, b: str, lineage: Lineage) -> bool:
    """True if `a` and `b` are the same dataset or one feeds the other."""
    if a == b:
        return True
    known = lineage.tables
    if a not in known or b not in known:
        return False
    return a in lineage.ancestors(b) or b in lineage.ancestors(a)


def root_candidate(datasets: Iterable[str], lineage: Lineage) -> str:
    """Most-upstream alerted dataset; datasets unknown to lineage come last."""
    datasets = set(datasets)
    known = datasets & lineage.tables
    ordered = lineage.topo_order(known) if known else []
    return [*ordered, *sorted(datasets - known)][0]


def upstream_first(candidates: Iterable[AlertCandidate], lineage: Lineage) -> list[AlertCandidate]:
    """Order a batch so upstream alerts open incidents and downstream symptoms attach to them.

    Incidents are never merged, so ingesting two sibling symptoms before their shared cause would
    open two incidents; within a batch this ordering prevents that.
    """
    candidates = list(candidates)
    known = {c.dataset for c in candidates} & lineage.tables
    rank = {t: i for i, t in enumerate(lineage.topo_order(known) if known else [])}
    return sorted(candidates, key=lambda c: (rank.get(c.dataset, len(rank)), c.dataset))


def ingest(
    candidate: AlertCandidate,
    store: AlertStore,
    lineage: Lineage,
    now: datetime,
    *,
    dedup_window: timedelta = DEDUP_WINDOW,
    debounce: timedelta = TRIAGE_DEBOUNCE,
) -> IngestResult:
    fp = fingerprint(candidate.detector, candidate.dataset, candidate.error_class)

    recent = store.find_recent_alert(fp, since=now - dedup_window)
    if recent is not None:
        alert_id, incident_id = recent
        store.bump_alert(alert_id, now)
        return IngestResult("deduped", alert_id, incident_id)

    match = next(
        (
            (iid, ds)
            for iid, ds in store.open_incidents()
            if any(same_lineage_path(candidate.dataset, d, lineage) for d in ds)
        ),
        None,
    )
    action: Literal["attached", "opened"]
    if match is not None:
        (incident_id, datasets), action = match, "attached"
    else:
        incident_id, datasets, action = store.create_incident(now), set(), "opened"

    alert_id = store.insert_alert(candidate, fp, incident_id, now)
    root = root_candidate({*datasets, candidate.dataset}, lineage)
    store.set_root_candidate(incident_id, root, now)
    store.enqueue_triage(incident_id, run_after=now + debounce)
    return IngestResult(action, alert_id, incident_id, root)
