"""CLI.

python -m rca.monitor run --date YYYY-MM-DD
"""

import argparse
import sys
from datetime import UTC, date, datetime

from rca.config import get_settings
from rca.db import connect
from rca.lineage.build import load_edges
from rca.lineage.graph import LineageGraph
from rca.monitor.config import load_config
from rca.monitor.detectors import run_detectors
from rca.monitor.store import ingest_all
from rca.observability.logging import configure_logging


def main() -> int:
    parser = argparse.ArgumentParser(prog="python -m rca.monitor")
    sub = parser.add_subparsers(dest="command", required=True)
    run_p = sub.add_parser("run", help="run all detectors for a date and ingest the alerts")
    run_p.add_argument("--date", type=date.fromisoformat, required=True)
    args = parser.parse_args()

    settings = get_settings()
    configure_logging("WARNING")
    config = load_config(settings.monitors_config)

    with connect(settings.database_url, autocommit=True) as conn:
        graph = LineageGraph(load_edges(conn))
        candidates = run_detectors(conn, config, graph, args.date)
        results = ingest_all(conn, candidates, graph, datetime.now(UTC))

        print(f"{args.date}: {len(candidates)} alert(s)")
        for c, r in results:
            print(f"  [{c.severity:<8}] {c.detector:<12} {c.dataset:<28} {c.error_class:<22}"
                  f" -> incident {r.incident_id} ({r.action})")  # fmt: skip

        incident_ids = sorted({r.incident_id for _, r in results})
        if incident_ids:
            rows = conn.execute(
                "SELECT i.incident_id, i.status, i.root_candidate, count(a.*) AS alerts,"
                " sum(a.count) AS occurrences, j.status AS job_status, j.run_after"
                " FROM ops.incidents i JOIN ops.alerts a USING (incident_id)"
                " LEFT JOIN ops.jobs j ON j.incident_id = i.incident_id AND j.kind = 'triage'"
                " WHERE i.incident_id = ANY(%s)"
                " GROUP BY i.incident_id, j.status, j.run_after ORDER BY i.incident_id",
                (incident_ids,),
            )
            print("incidents:")
            for row in rows:
                print(f"  #{row['incident_id']} {row['status']:<8} root={row['root_candidate']:<26}"
                      f" alerts={row['alerts']} occurrences={row['occurrences']}"
                      f" triage={row['job_status']}")  # fmt: skip
    return 0


if __name__ == "__main__":
    sys.exit(main())
