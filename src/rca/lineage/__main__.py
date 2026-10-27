"""CLI.

python -m rca.lineage build
python -m rca.lineage show TABLE [--direction upstream|downstream] [--depth N] [--columns]
"""

import argparse
import sys

from rca.config import get_settings
from rca.db import connect
from rca.lineage.build import build_all, load_edges, save_edges, warehouse_schema
from rca.lineage.graph import LineageGraph
from rca.observability.logging import configure_logging


def main() -> int:
    parser = argparse.ArgumentParser(prog="python -m rca.lineage")
    sub = parser.add_subparsers(dest="command", required=True)
    sub.add_parser("build", help="parse every DAG's SQL and replace lineage.edges")
    show = sub.add_parser("show", help="print lineage around a table")
    show.add_argument("table")
    show.add_argument("--direction", choices=["upstream", "downstream"], default="upstream")
    show.add_argument("--depth", type=int, default=2)
    show.add_argument("--columns", action="store_true", help="include column-level edges")
    args = parser.parse_args()

    settings = get_settings()
    configure_logging("ERROR")  # warnings are printed below instead

    with connect(settings.database_url, autocommit=True) as conn:
        if args.command == "build":
            results = build_all(settings.dags_dir, warehouse_schema(conn))
            edges = [e for r in results for e in r.edges]
            save_edges(conn, edges)
            n_columns = sum(e.dst_column is not None for e in edges)
            print(f"saved {len(edges)} edges ({n_columns} column-level) from {len(results)} tasks")
            for warning in (w for r in results for w in r.warnings):
                print(f"WARNING {warning}")
            return 0

        graph = LineageGraph(load_edges(conn))
        sg = graph.upstream(args.table, args.depth) if args.direction == "upstream" else (
            graph.downstream(args.table, args.depth)
        )  # fmt: skip
        print(f"{args.direction} of {args.table} (depth {args.depth}):")
        for table in sorted(sg.hops, key=lambda t: (sg.hops[t], t)):
            print(f"  {sg.hops[table]}  {table}")
        for e in sg.edges:
            if e.dst_column is None:
                print(f"  {e.src_table or '<external>'} -> {e.dst_table}  [{e.task_id}]")
            elif args.columns:
                print(f"    {e.src_table}.{e.src_column} -> {e.dst_table}.{e.dst_column}")
    return 0


if __name__ == "__main__":
    sys.exit(main())
