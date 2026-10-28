"""Retrieval eval: recall@k and MRR of each scenario's expected_doc_ids, per retrieval mode.

Query = the scenario's `symptoms` (what on-call would search, never the answer). Results are
ranked at document level (chunk hits deduplicated by doc_id), independent of the agent.

    python eval/retrieval_eval.py [--k 5] [--embedder fastembed|hash] [--reindex]

Writes eval/reports/retrieval.md and prints it.
"""

import argparse
import sys
from dataclasses import dataclass
from datetime import UTC, datetime
from pathlib import Path
from statistics import fmean

from rca.config import get_settings
from rca.db import connect
from rca.faults.scenario import load_scenarios
from rca.knowledge.embed import make_embedder
from rca.knowledge.index import index_oncall
from rca.knowledge.metrics import dedupe, recall_at_k, reciprocal_rank
from rca.knowledge.search import Mode, search
from rca.observability.logging import configure_logging

MODES: tuple[Mode, ...] = ("vector", "keyword", "hybrid")


@dataclass
class Row:
    scenario: str
    expected: list[str]
    ranked: dict[Mode, list[str]]
    recall: dict[Mode, float]
    rr: dict[Mode, float]


def render(rows: list[Row], k: int, embedder: str) -> str:
    out = [
        "# Retrieval eval",
        "",
        f"Generated {datetime.now(UTC):%Y-%m-%d %H:%M} UTC · embedder `{embedder}` · k={k} · "
        f"{len(rows)} scenarios · query = scenario `symptoms`",
        "",
        "## Summary",
        "",
        f"| mode | mean recall@{k} | MRR | scenarios with all expected docs in top {k} |",
        "|---|---|---|---|",
    ]
    for mode in MODES:
        full = sum(r.recall[mode] == 1.0 for r in rows)
        out.append(
            f"| {mode} | {fmean(r.recall[mode] for r in rows):.2f} | "
            f"{fmean(r.rr[mode] for r in rows):.2f} | {full}/{len(rows)} |"
        )
    out += [
        "",
        "## Per scenario (recall@k / reciprocal rank)",
        "",
        "| scenario | expected | " + " | ".join(MODES) + " |",
        "|---|---|" + "---|" * len(MODES),
    ]
    for r in rows:
        cells = " | ".join(f"{r.recall[m]:.2f} / {r.rr[m]:.2f}" for m in MODES)
        out.append(f"| {r.scenario} | {', '.join(r.expected)} | {cells} |")

    misses = [r for r in rows if any(r.recall[m] < 1.0 for m in MODES)]
    if misses:
        out += ["", f"## Misses (top {k} documents per mode)", ""]
        for r in misses:
            out.append(f"**{r.scenario}** — expected {', '.join(r.expected)}")
            for m in MODES:
                if r.recall[m] < 1.0:
                    out.append(f"- {m}: {', '.join(r.ranked[m][:k]) or '(no hits)'}")
            out.append("")
    return "\n".join(out).rstrip() + "\n"


def main() -> int:
    parser = argparse.ArgumentParser()
    parser.add_argument("--k", type=int, default=5)
    parser.add_argument("--embedder", choices=["fastembed", "hash"], default=None)
    parser.add_argument("--candidates", type=int, default=20, help="chunks fetched per retriever")
    parser.add_argument("--reindex", action="store_true", help="rebuild kb.chunks first")
    parser.add_argument("--out", type=Path, default=Path("eval/reports/retrieval.md"))
    args = parser.parse_args()

    settings = get_settings()
    configure_logging("WARNING")
    embedder = make_embedder(
        args.embedder or settings.embedder, settings.embedding_model, settings.embedding_cache_dir
    )
    scenarios = load_scenarios(settings.scenarios_dir)

    rows = []
    with connect(settings.database_url, autocommit=True) as conn:
        if args.reindex:
            index_oncall(conn, settings.oncall_dir, embedder)
        for scenario in scenarios.values():
            expected = scenario.ground_truth.expected_doc_ids
            ranked: dict[Mode, list[str]] = {}
            for mode in MODES:
                hits = search(
                    conn, embedder, scenario.symptoms,
                    mode=mode, k=args.candidates, candidates=args.candidates,
                )  # fmt: skip
                ranked[mode] = dedupe(h.doc_id for h in hits)
            rows.append(
                Row(
                    scenario.id,
                    expected,
                    ranked,
                    {m: recall_at_k(ranked[m], expected, args.k) for m in MODES},
                    {m: reciprocal_rank(ranked[m], expected) for m in MODES},
                )
            )

    report = render(rows, args.k, embedder.name)
    args.out.parent.mkdir(parents=True, exist_ok=True)
    args.out.write_text(report, encoding="utf-8")
    print(report)
    return 0


if __name__ == "__main__":
    sys.exit(main())
