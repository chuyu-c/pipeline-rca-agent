"""CLI.

python -m rca.knowledge index [--embedder fastembed|hash]
python -m rca.knowledge search "query" [--mode hybrid|vector|keyword] [-k 5] [--doc-type runbook]
"""

import argparse
import sys

from rca.config import get_settings
from rca.db import connect
from rca.knowledge.embed import make_embedder
from rca.knowledge.index import index_oncall
from rca.knowledge.search import search
from rca.observability.logging import configure_logging


def main() -> int:
    parser = argparse.ArgumentParser(prog="python -m rca.knowledge")
    parser.add_argument("--embedder", choices=["fastembed", "hash"], default=None)
    sub = parser.add_subparsers(dest="command", required=True)
    sub.add_parser("index", help="chunk, embed and (re)build kb.chunks from oncall/")
    s = sub.add_parser("search", help="query the knowledge base")
    s.add_argument("query")
    s.add_argument("--mode", choices=["hybrid", "vector", "keyword"], default="hybrid")
    s.add_argument("-k", type=int, default=5)
    s.add_argument("--doc-type", choices=["runbook", "postmortem"], default=None)
    args = parser.parse_args()

    settings = get_settings()
    configure_logging("WARNING")
    embedder = make_embedder(
        args.embedder or settings.embedder, settings.embedding_model, settings.embedding_cache_dir
    )

    with connect(settings.database_url, autocommit=True) as conn:
        if args.command == "index":
            n = index_oncall(conn, settings.oncall_dir, embedder)
            print(f"indexed {n} chunks from {settings.oncall_dir} with {embedder.name}")
            return 0
        hits = search(conn, embedder, args.query, mode=args.mode, k=args.k, doc_type=args.doc_type)
        for i, h in enumerate(hits, 1):
            ranks = " ".join(f"{m}#{r}" for m, r in sorted(h.ranks.items()))
            print(f"{i}. {h.chunk_id:<52} score={h.score:.4f}  [{ranks}]")
            print(f"   {h.section}")
    return 0


if __name__ == "__main__":
    sys.exit(main())
