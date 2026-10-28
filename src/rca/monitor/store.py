"""Postgres `AlertStore` and the transactional wrapper around `ingest`."""

from collections.abc import Iterable
from datetime import date, datetime

from psycopg.types.json import Jsonb

from rca.db import Connection
from rca.lineage.build import load_edges
from rca.lineage.graph import LineageGraph
from rca.monitor.config import MonitorConfig
from rca.monitor.detectors import AlertCandidate, run_detectors
from rca.monitor.ingest import IngestResult, Lineage, ingest, upstream_first

# Serializes ingest across processes so two alerts can't both open an incident for one break.
_INGEST_LOCK = 0x7263615F616C7274  # "rca_alrt"


class PgAlertStore:
    def __init__(self, conn: Connection) -> None:
        self.conn = conn

    def find_recent_alert(self, fingerprint: str, since: datetime) -> tuple[int, int] | None:
        row = self.conn.execute(
            "SELECT a.alert_id, a.incident_id FROM ops.alerts a"
            " JOIN ops.incidents i USING (incident_id)"
            " WHERE a.fingerprint = %s AND a.last_seen >= %s AND i.status <> 'resolved'"
            " ORDER BY a.last_seen DESC LIMIT 1",
            (fingerprint, since),
        ).fetchone()
        return (row["alert_id"], row["incident_id"]) if row else None

    def bump_alert(self, alert_id: int, now: datetime) -> None:
        self.conn.execute(
            "UPDATE ops.alerts SET count = count + 1, last_seen = %s WHERE alert_id = %s",
            (now, alert_id),
        )

    def open_incidents(self) -> list[tuple[int, set[str]]]:
        rows = self.conn.execute(
            "SELECT i.incident_id, array_agg(DISTINCT a.dataset) AS datasets"
            " FROM ops.incidents i JOIN ops.alerts a USING (incident_id)"
            " WHERE i.status <> 'resolved'"
            " GROUP BY i.incident_id, i.created_at ORDER BY i.created_at, i.incident_id"
        )
        return [(r["incident_id"], set(r["datasets"])) for r in rows]

    def create_incident(self, now: datetime) -> int:
        row = self.conn.execute(
            "INSERT INTO ops.incidents (created_at, updated_at) VALUES (%s, %s)"
            " RETURNING incident_id",
            (now, now),
        ).fetchone()
        assert row is not None
        return int(row["incident_id"])

    def insert_alert(
        self, candidate: AlertCandidate, fingerprint: str, incident_id: int, now: datetime
    ) -> int:
        c = candidate
        row = self.conn.execute(
            "INSERT INTO ops.alerts (fingerprint, detector, dataset, error_class, severity,"
            " logical_date, payload, first_seen, last_seen, incident_id)"
            " VALUES (%s, %s, %s, %s, %s, %s, %s, %s, %s, %s) RETURNING alert_id",
            (fingerprint, c.detector, c.dataset, c.error_class, c.severity, c.logical_date,
             Jsonb(c.payload), now, now, incident_id),
        ).fetchone()  # fmt: skip
        assert row is not None
        return int(row["alert_id"])

    def set_root_candidate(self, incident_id: int, root: str, now: datetime) -> None:
        self.conn.execute(
            "UPDATE ops.incidents SET root_candidate = %s, updated_at = %s WHERE incident_id = %s",
            (root, now, incident_id),
        )

    def enqueue_triage(self, incident_id: int, run_after: datetime) -> None:
        self.conn.execute(
            "INSERT INTO ops.jobs (kind, incident_id, payload, run_after)"
            " VALUES ('triage', %s, %s, %s)"
            " ON CONFLICT (incident_id, kind) DO UPDATE"
            " SET run_after = greatest(ops.jobs.run_after, EXCLUDED.run_after)"
            " WHERE ops.jobs.status = 'queued'",
            (incident_id, Jsonb({"incident_id": incident_id}), run_after),
        )


def run_monitors(
    conn: Connection, config: MonitorConfig, logical_date: date, now: datetime
) -> list[tuple[AlertCandidate, IngestResult]]:
    """Detect alerts for `logical_date` against the stored lineage graph and ingest them."""
    graph = LineageGraph(load_edges(conn))
    return ingest_all(conn, run_detectors(conn, config, graph, logical_date), graph, now)


def format_incidents(conn: Connection, incident_ids: Iterable[int]) -> str:
    rows = conn.execute(
        "SELECT i.incident_id, i.status, i.root_candidate, count(a.*) AS alerts,"
        " sum(a.count) AS occurrences, j.status AS job_status"
        " FROM ops.incidents i JOIN ops.alerts a USING (incident_id)"
        " LEFT JOIN ops.jobs j ON j.incident_id = i.incident_id AND j.kind = 'triage'"
        " WHERE i.incident_id = ANY(%s)"
        " GROUP BY i.incident_id, j.status ORDER BY i.incident_id",
        (sorted(set(incident_ids)),),
    )
    return "\n".join(
        f"  #{r['incident_id']} {r['status']:<8} root_candidate={r['root_candidate']}"
        f"  alerts={r['alerts']} occurrences={r['occurrences']} triage={r['job_status']}"
        for r in rows
    )


def format_alerts(results: Iterable[tuple[AlertCandidate, IngestResult]]) -> str:
    return "\n".join(
        f"  [{c.severity:<8}] {c.detector:<12} {c.dataset:<26} {c.error_class:<20}"
        f" -> incident #{r.incident_id} ({r.action})"
        for c, r in results
    )


def ingest_all(
    conn: Connection, candidates: Iterable[AlertCandidate], lineage: Lineage, now: datetime
) -> list[tuple[AlertCandidate, IngestResult]]:
    """Ingest a batch upstream-first, each candidate in its own transaction under an advisory lock.

    Returns (candidate, result) pairs in ingest order.
    """
    store = PgAlertStore(conn)
    results = []
    for candidate in upstream_first(candidates, lineage):
        with conn.transaction():
            conn.execute("SELECT pg_advisory_xact_lock(%s)", (_INGEST_LOCK,))
            results.append((candidate, ingest(candidate, store, lineage, now)))
    return results
