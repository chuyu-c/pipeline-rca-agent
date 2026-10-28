"""CLI.

python -m rca.faults list
python -m rca.faults inject <scenario> [--date YYYY-MM-DD]
python -m rca.faults reset [--date YYYY-MM-DD]
python -m rca.faults demo <scenario> [--date YYYY-MM-DD]
"""

import argparse
import sys
from datetime import date

from rca.config import get_settings
from rca.db import connect
from rca.faults.demo import run_demo
from rca.faults.inject import inject, reset
from rca.faults.scenario import load_scenarios
from rca.observability.logging import configure_logging


def main() -> int:
    parser = argparse.ArgumentParser(prog="python -m rca.faults")
    sub = parser.add_subparsers(dest="command", required=True)
    sub.add_parser("list", help="list scenarios")
    for name, help_text in (
        ("inject", "apply a scenario's setup (does not reset first)"),
        ("demo", "reset, inject, run the pipeline and monitors, print the incident"),
    ):
        p = sub.add_parser(name, help=help_text)
        p.add_argument("scenario")
        p.add_argument("--date", type=date.fromisoformat, default=date.today())
        p.add_argument("--dag", default="ecommerce_daily")
    reset_p = sub.add_parser("reset", help="undo all scenarios and resolve open incidents")
    reset_p.add_argument("--date", type=date.fromisoformat, default=date.today())
    args = parser.parse_args()

    settings = get_settings()
    configure_logging("ERROR")  # the demo prints its own summary
    scenarios = load_scenarios(settings.scenarios_dir)

    if args.command == "list":
        for s in scenarios.values():
            print(f"{s.id:<16} {s.ground_truth.category:<15} {s.title}")
        return 0
    if args.command == "demo":
        return run_demo(settings, args.scenario, args.date, args.dag)
    if args.command == "inject" and args.scenario not in scenarios:
        print(f"unknown scenario {args.scenario!r}; choose from: {', '.join(scenarios)}")
        return 2

    with connect(settings.admin_database_url, autocommit=True) as admin:
        if args.command == "inject":
            inject(admin, scenarios[args.scenario], args.date, settings.dags_dir)
            print(f"injected {args.scenario}; run the pipeline for {args.date} to see it")
        else:
            reset(admin, scenarios.values(), args.date, settings.dags_dir)
            print(f"reset: faults cleared, incidents resolved, {args.date} ready for a fresh run")
    return 0


if __name__ == "__main__":
    sys.exit(main())
