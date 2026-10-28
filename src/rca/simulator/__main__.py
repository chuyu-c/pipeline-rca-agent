"""CLI.

python -m rca.simulator run --date YYYY-MM-DD [--dag ecommerce_daily]
python -m rca.simulator seed [--days 14] [--end-date YYYY-MM-DD]
"""

import argparse
import sys
from datetime import date, timedelta

from rca.config import Settings, get_settings
from rca.db import connect
from rca.observability.logging import configure_logging
from rca.simulator.dag import load_dag
from rca.simulator.ingest import load_products
from rca.simulator.runner import DagRunResult, format_run, run_pg


def _run(settings: Settings, dag_id: str, logical_date: date) -> DagRunResult:
    dag = load_dag(settings.dags_dir / f"{dag_id}.yaml")
    return run_pg(settings.database_url, dag, logical_date, settings.sim_seed)


def main() -> int:
    parser = argparse.ArgumentParser(prog="python -m rca.simulator")
    sub = parser.add_subparsers(dest="command", required=True)

    run_p = sub.add_parser("run", help="run the DAG for one logical date")
    run_p.add_argument("--date", type=date.fromisoformat, required=True)
    run_p.add_argument("--dag", default="ecommerce_daily")

    seed_p = sub.add_parser("seed", help="load products and backfill N days of partitions")
    seed_p.add_argument("--days", type=int, default=14)
    seed_p.add_argument("--end-date", type=date.fromisoformat, default=date.today())
    seed_p.add_argument("--dag", default="ecommerce_daily")

    args = parser.parse_args()
    settings = get_settings()
    configure_logging("WARNING")  # keep CLI output readable; task logs go to ops.task_logs

    if args.command == "run":
        result = _run(settings, args.dag, args.date)
        print(format_run(args.date, result))
        return 0 if result.ok else 1

    with connect(settings.database_url, autocommit=True) as conn:
        print(f"loaded {load_products(conn, settings.sim_seed)} products")
    ok = True
    for offset in range(args.days - 1, -1, -1):
        logical_date = args.end_date - timedelta(days=offset)
        result = _run(settings, args.dag, logical_date)
        print(format_run(logical_date, result))
        ok &= result.ok
    return 0 if ok else 1


if __name__ == "__main__":
    sys.exit(main())
