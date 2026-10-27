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
from rca.simulator.runner import DagRunResult, PgExecutor, PgRunStore, run_dag


def _run(settings: Settings, dag_id: str, logical_date: date) -> DagRunResult:
    dag = load_dag(settings.dags_dir / f"{dag_id}.yaml")
    with (
        connect(settings.database_url, autocommit=True) as work_conn,
        connect(settings.database_url, autocommit=True) as ops_conn,
    ):
        return run_dag(
            dag, logical_date, PgExecutor(work_conn, settings.sim_seed), PgRunStore(ops_conn)
        )


def _print_summary(logical_date: date, result: DagRunResult) -> None:
    print(f"{logical_date}  dag_run={result.dag_run_id}")
    for task_id, run in result.final.items():
        error = f"  {run.error_class}: {run.error_message}" if run.error_class else ""
        print(f"  {task_id:<28} {run.status:<16} try={run.try_number}{error}")


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
        _print_summary(args.date, result)
        return 0 if result.ok else 1

    with connect(settings.database_url, autocommit=True) as conn:
        print(f"loaded {load_products(conn, settings.sim_seed)} products")
    ok = True
    for offset in range(args.days - 1, -1, -1):
        logical_date = args.end_date - timedelta(days=offset)
        result = _run(settings, args.dag, logical_date)
        _print_summary(logical_date, result)
        ok &= result.ok
    return 0 if ok else 1


if __name__ == "__main__":
    sys.exit(main())
