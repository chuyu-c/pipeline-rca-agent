from dataclasses import dataclass, field
from datetime import UTC, date, datetime, timedelta
from itertools import permutations

import pytest

from rca.lineage.graph import LineageGraph
from rca.lineage.parser import Edge
from rca.monitor.detectors import AlertCandidate
from rca.monitor.ingest import (
    DEDUP_WINDOW,
    TRIAGE_DEBOUNCE,
    fingerprint,
    ingest,
    root_candidate,
    upstream_first,
)

T0 = datetime(2026, 3, 2, 6, 0, tzinfo=UTC)
D = date(2026, 3, 2)


# --------------------------------------------------------------------------- fakes


@dataclass
class FakeAlert:
    alert_id: int
    fingerprint: str
    dataset: str
    incident_id: int
    first_seen: datetime
    last_seen: datetime
    count: int = 1


@dataclass
class FakeIncident:
    incident_id: int
    created_at: datetime
    status: str = "open"
    root_candidate: str | None = None


@dataclass
class FakeJob:
    incident_id: int
    run_after: datetime
    status: str = "queued"


@dataclass
class FakeAlertStore:
    alerts: list[FakeAlert] = field(default_factory=list)
    incidents: dict[int, FakeIncident] = field(default_factory=dict)
    jobs: dict[int, FakeJob] = field(default_factory=dict)

    def find_recent_alert(self, fingerprint: str, since: datetime) -> tuple[int, int] | None:
        matches = [
            a for a in self.alerts
            if a.fingerprint == fingerprint and a.last_seen >= since
            and self.incidents[a.incident_id].status != "resolved"
        ]  # fmt: skip
        latest = max(matches, key=lambda a: a.last_seen, default=None)
        return (latest.alert_id, latest.incident_id) if latest else None

    def bump_alert(self, alert_id: int, now: datetime) -> None:
        alert = next(a for a in self.alerts if a.alert_id == alert_id)
        alert.count += 1
        alert.last_seen = now

    def open_incidents(self) -> list[tuple[int, set[str]]]:
        return [
            (i.incident_id, {a.dataset for a in self.alerts if a.incident_id == i.incident_id})
            for i in sorted(self.incidents.values(), key=lambda i: (i.created_at, i.incident_id))
            if i.status != "resolved"
        ]

    def create_incident(self, now: datetime) -> int:
        iid = len(self.incidents) + 1
        self.incidents[iid] = FakeIncident(iid, now)
        return iid

    def insert_alert(
        self, candidate: AlertCandidate, fingerprint: str, incident_id: int, now: datetime
    ) -> int:
        aid = len(self.alerts) + 1
        self.alerts.append(FakeAlert(aid, fingerprint, candidate.dataset, incident_id, now, now))
        return aid

    def set_root_candidate(self, incident_id: int, root: str, now: datetime) -> None:
        self.incidents[incident_id].root_candidate = root

    def enqueue_triage(self, incident_id: int, run_after: datetime) -> None:
        job = self.jobs.get(incident_id)
        if job is None:
            self.jobs[incident_id] = FakeJob(incident_id, run_after)
        elif job.status == "queued":
            job.run_after = max(job.run_after, run_after)


@pytest.fixture
def lineage() -> LineageGraph:
    """
    raw.a -> stg.a -> mart.x
                   -> mart.y
    raw.b -> stg.b -> mart.z
    """
    return LineageGraph([
        Edge("raw.a", None, "stg.a", None, "stg_a"),
        Edge("stg.a", None, "mart.x", None, "mart_x"),
        Edge("stg.a", None, "mart.y", None, "mart_y"),
        Edge("raw.b", None, "stg.b", None, "stg_b"),
        Edge("stg.b", None, "mart.z", None, "mart_z"),
    ])  # fmt: skip


@pytest.fixture
def store() -> FakeAlertStore:
    return FakeAlertStore()


def alert(
    dataset: str, detector: str = "freshness", error: str = "partition_missing"
) -> AlertCandidate:
    return AlertCandidate(detector, dataset, error, "high", D)


# --------------------------------------------------------------------------- fingerprint


def test_fingerprint_is_deterministic_and_field_sensitive() -> None:
    fp = fingerprint("freshness", "mart.x", "partition_missing")
    assert fp == fingerprint("freshness", "mart.x", "partition_missing")
    assert len(fp) == 64
    assert fp != fingerprint("volume", "mart.x", "partition_missing")
    assert fp != fingerprint("freshness", "mart.y", "partition_missing")
    assert fp != fingerprint("freshness", "mart.x", "volume_high")


def test_fingerprint_fields_cannot_bleed_into_each_other() -> None:
    assert fingerprint("a|b", "c", "d") != fingerprint("a", "b|c", "d")
    assert fingerprint("ab", "c", "d") != fingerprint("a", "bc", "d")


# --------------------------------------------------------------------------- dedup window


def test_same_fingerprint_inside_window_is_deduped(
    store: FakeAlertStore, lineage: LineageGraph
) -> None:
    first = ingest(alert("mart.x"), store, lineage, T0)
    second = ingest(alert("mart.x"), store, lineage, T0 + DEDUP_WINDOW - timedelta(seconds=1))

    assert (first.action, second.action) == ("opened", "deduped")
    assert second.alert_id == first.alert_id
    assert len(store.alerts) == 1 and store.alerts[0].count == 2
    assert len(store.incidents) == 1


def test_window_slides_with_last_seen(store: FakeAlertStore, lineage: LineageGraph) -> None:
    ingest(alert("mart.x"), store, lineage, T0)
    ingest(alert("mart.x"), store, lineage, T0 + timedelta(minutes=25))
    # 50 min after first_seen but only 25 after last_seen: still the same alert.
    assert ingest(alert("mart.x"), store, lineage, T0 + timedelta(minutes=50)).action == "deduped"
    assert store.alerts[0].count == 3


def test_same_fingerprint_after_window_is_a_new_alert(
    store: FakeAlertStore, lineage: LineageGraph
) -> None:
    ingest(alert("mart.x"), store, lineage, T0)
    later = ingest(alert("mart.x"), store, lineage, T0 + DEDUP_WINDOW + timedelta(seconds=1))

    assert later.action == "attached"  # new alert row, same (still open) incident
    assert len(store.alerts) == 2 and len(store.incidents) == 1


def test_different_error_class_is_not_deduped(store: FakeAlertStore, lineage: LineageGraph) -> None:
    ingest(alert("mart.x", "volume", "volume_high"), store, lineage, T0)
    other = ingest(alert("mart.x", "volume", "volume_low"), store, lineage, T0)
    assert other.action == "attached" and len(store.alerts) == 2


def test_dedup_ignores_resolved_incidents(store: FakeAlertStore, lineage: LineageGraph) -> None:
    first = ingest(alert("mart.x"), store, lineage, T0)
    store.incidents[first.incident_id].status = "resolved"
    again = ingest(alert("mart.x"), store, lineage, T0 + timedelta(minutes=1))
    assert again.action == "opened" and again.incident_id != first.incident_id


# --------------------------------------------------------------------------- correlation


def test_upstream_break_and_downstream_symptoms_form_one_incident(
    store: FakeAlertStore, lineage: LineageGraph
) -> None:
    results = [
        ingest(alert("stg.a", "task_failure", "UndefinedColumn"), store, lineage, T0),
        ingest(alert("mart.x"), store, lineage, T0),
        ingest(alert("mart.y"), store, lineage, T0),
        ingest(alert("stg.a"), store, lineage, T0),  # its own freshness alert
    ]
    assert [r.action for r in results] == ["opened", "attached", "attached", "attached"]
    assert len(store.incidents) == 1
    assert store.incidents[1].root_candidate == "stg.a"


@pytest.mark.parametrize("order", list(permutations(["mart.x", "stg.a", "raw.a"])))
def test_root_candidate_is_most_upstream_regardless_of_arrival_order(
    order: tuple[str, ...], lineage: LineageGraph
) -> None:
    store = FakeAlertStore()
    for dataset in order:
        ingest(alert(dataset), store, lineage, T0)
    assert len(store.incidents) == 1
    assert store.incidents[1].root_candidate == "raw.a"


def test_independent_branches_stay_separate(store: FakeAlertStore, lineage: LineageGraph) -> None:
    a = ingest(alert("stg.a"), store, lineage, T0)
    b = ingest(alert("stg.b"), store, lineage, T0)
    z = ingest(alert("mart.z"), store, lineage, T0)

    assert a.incident_id != b.incident_id
    assert z.incident_id == b.incident_id
    assert store.incidents[b.incident_id].root_candidate == "stg.b"


def test_siblings_under_one_table_do_not_correlate_without_it(
    store: FakeAlertStore, lineage: LineageGraph
) -> None:
    # mart.x and mart.y share ancestor stg.a, but neither feeds the other.
    x = ingest(alert("mart.x"), store, lineage, T0)
    y = ingest(alert("mart.y"), store, lineage, T0)
    assert x.incident_id != y.incident_id


def test_dataset_unknown_to_lineage_gets_its_own_incident(
    store: FakeAlertStore, lineage: LineageGraph
) -> None:
    ingest(alert("stg.a"), store, lineage, T0)
    unknown = ingest(alert("task:mystery", "task_failure", "Boom"), store, lineage, T0)
    again = ingest(alert("task:mystery", "task_failure", "Other"), store, lineage, T0)

    assert unknown.action == "opened"
    assert again.incident_id == unknown.incident_id  # same dataset still correlates
    assert store.incidents[unknown.incident_id].root_candidate == "task:mystery"


def test_resolved_incidents_are_not_reused(store: FakeAlertStore, lineage: LineageGraph) -> None:
    first = ingest(alert("stg.a"), store, lineage, T0)
    store.incidents[first.incident_id].status = "resolved"
    later = ingest(alert("mart.x"), store, lineage, T0)
    assert later.action == "opened"


def test_upstream_first_orders_a_batch_so_siblings_join_their_cause(lineage: LineageGraph) -> None:
    batch = [alert("mart.x"), alert("mart.y"), alert("stg.a", "task_failure", "Err")]
    ordered = upstream_first(batch, lineage)
    assert [c.dataset for c in ordered] == ["stg.a", "mart.x", "mart.y"]

    store = FakeAlertStore()
    for candidate in ordered:
        ingest(candidate, store, lineage, T0)
    assert len(store.incidents) == 1


def test_root_candidate_puts_unknown_datasets_last(lineage: LineageGraph) -> None:
    assert root_candidate({"mart.x", "aaa.unknown", "stg.a"}, lineage) == "stg.a"
    assert root_candidate({"zzz.unknown", "aaa.unknown"}, lineage) == "aaa.unknown"


# --------------------------------------------------------------------------- triage enqueue


def test_one_debounced_triage_job_per_incident(
    store: FakeAlertStore, lineage: LineageGraph
) -> None:
    ingest(alert("stg.a"), store, lineage, T0)
    ingest(alert("mart.x"), store, lineage, T0 + timedelta(seconds=30))
    ingest(alert("stg.b"), store, lineage, T0)

    assert set(store.jobs) == {1, 2}
    # The second alert pushed incident 1's job back so correlated alerts land before triage.
    assert store.jobs[1].run_after == T0 + timedelta(seconds=30) + TRIAGE_DEBOUNCE


def test_enqueue_does_not_touch_a_job_already_running(
    store: FakeAlertStore, lineage: LineageGraph
) -> None:
    ingest(alert("stg.a"), store, lineage, T0)
    store.jobs[1].status = "running"
    ingest(alert("mart.x"), store, lineage, T0 + timedelta(minutes=5))
    assert store.jobs[1].run_after == T0 + TRIAGE_DEBOUNCE


def test_tables_written_by_the_same_task_correlate(lineage: LineageGraph) -> None:
    # One ingest load writes both raw.a and raw.b (like raw.orders + raw.order_items).
    shared = LineageGraph([
        *lineage.edges,
        Edge(None, None, "raw.a", None, "ingest"),
        Edge(None, None, "raw.b", None, "ingest"),
    ])  # fmt: skip
    store = FakeAlertStore()
    batch = [alert(d, "volume", "volume_high") for d in ("raw.a", "raw.b", "stg.a", "stg.b")]
    for candidate in upstream_first(batch, shared):
        ingest(candidate, store, shared, T0)
    assert len(store.incidents) == 1

    # Without the shared producer, the two branches stay separate.
    separate = FakeAlertStore()
    for candidate in upstream_first(batch, lineage):
        ingest(candidate, separate, lineage, T0)
    assert len(separate.incidents) == 2
