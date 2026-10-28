"""CLI.

python -m rca.monitor run --date YYYY-MM-DD
"""

import argparse
import sys
from datetime import UTC, date, datetime

from rca.config import get_settings
from rca.db import connect
from rca.monitor.config import load_config
from rca.monitor.store import format_alerts, format_incidents, run_monitors
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
        results = run_monitors(conn, config, args.date, datetime.now(UTC))
        print(f"{args.date}: {len(results)} alert(s)")
        if results:
            print(format_alerts(results))
            print("incidents:")
            print(format_incidents(conn, (r.incident_id for _, r in results)))
    return 0


if __name__ == "__main__":
    sys.exit(main())
