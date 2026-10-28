"""End-to-end eval: for each scenario, reset → inject → run the pipeline → monitors → triage →
score against ground truth. Writes a markdown report and a JSON file next to it.

    python eval/run_eval.py [--llm replay|live] [--baseline] [--retrieval vector|hybrid]
                            [--scenarios a,b] [--date YYYY-MM-DD] [--record]

--llm replay   replays tests/fixtures/llm/<scenario>.json (or <scenario>.synthetic.json);
               scenarios without a transcript are reported as skipped. Free and deterministic.
--llm live     calls the configured model (costs money). With --record, saves transcripts.
--baseline     deterministic fallback only (no LLM) -> eval/reports/baseline.md; later runs report
               their lift over it.

Runs against the configured database (inside the app container: `make eval`). The logical date is
reset and re-run cleanly at the end, so the environment is left healthy.
"""

import argparse
import json
import sys
from collections import Counter
from datetime import UTC, date, datetime
from pathlib import Path

from rca.agent.llm import LLMClient, NoLLM, ReplayLLM
from rca.agent.loop import persist_outcome, run_triage
from rca.agent.service import make_llm
from rca.agent.tools import ToolContext
from rca.config import Settings, get_settings
from rca.db import connect
from rca.evaluation.report import render_markdown
from rca.evaluation.safety import diff_snapshots, obeyed_injection, snapshot, unsafe_attempts
from rca.evaluation.scoring import ScenarioResult, summarize
from rca.faults.inject import inject, reset
from rca.faults.scenario import Scenario, load_scenarios
from rca.knowledge.embed import Embedder, make_embedder
from rca.knowledge.search import Mode
from rca.monitor.config import load_config
from rca.monitor.store import run_monitors
from rca.observability.logging import configure_logging
from rca.simulator.dag import load_dag
from rca.simulator.runner import run_pg

FIXTURES = Path("tests/fixtures/llm")
REPORTS = Path("eval/reports")


def pick_llm(args: argparse.Namespace, settings: Settings, scenario_id: str) -> LLMClient | str:
    """The client for this scenario, or the reason it is skipped."""
    if args.baseline:
        return NoLLM()
    if args.llm == "replay":
        for name in (f"{scenario_id}.json", f"{scenario_id}.synthetic.json"):
            if (FIXTURES / name).exists():
                return ReplayLLM(FIXTURES / name)
        return "no transcript"
    record = FIXTURES / f"{scenario_id}.json" if args.record else None
    return make_llm(settings, "anthropic", record=record)


def run_scenario(
    scenario: Scenario,
    all_scenarios: list[Scenario],
    llm: LLMClient,
    settings: Settings,
    embedder: Embedder,
    logical_date: date,
    retrieval: Mode,
) -> ScenarioResult:
    truth = scenario.ground_truth
    result = ScenarioResult(scenario.id, truth.category, truth.root_entity, truth.expected_doc_ids)
    dag = load_dag(settings.dags_dir / "ecommerce_daily.yaml")

    with connect(settings.admin_database_url, autocommit=True) as admin:
        reset(admin, all_scenarios, logical_date, settings.dags_dir)
        inject(admin, scenario, logical_date, settings.dags_dir)
        run_pg(settings.database_url, dag, logical_date, settings.sim_seed)

        with (
            connect(settings.database_url, autocommit=True) as app,
            connect(settings.readonly_database_url, autocommit=True) as readonly,
        ):
            alerts = run_monitors(
                app, load_config(settings.monitors_config), logical_date, datetime.now(UTC)
            )
            if not alerts:
                result.skipped = "no alerts raised"
                return result
            counts = Counter(r.incident_id for _, r in alerts)
            result.alerts_per_incident = dict(counts)
            incident_id = counts.most_common(1)[0][0]

            before = snapshot(admin)
            ctx = ToolContext(app, readonly, embedder, incident_id, retrieval)
            outcome = run_triage(
                ctx, llm, max_steps=settings.max_steps, token_budget=settings.token_budget
            )
            persist_outcome(app, incident_id, outcome)
            after = snapshot(admin)

    report = outcome.report
    result.violations = diff_snapshots(before, after)
    if truth.category != "unknown" and obeyed_injection(report):
        result.violations.append("report follows the injected instruction (unknown @ 1.0)")
    result.source, result.model = outcome.source, outcome.model
    result.fallback_reason = outcome.fallback_reason
    result.category, result.root_entity = report.category, report.root_entity
    result.confidence, result.citations = report.confidence, report.citations
    result.summary = report.summary
    result.steps, result.latency_s = outcome.steps, outcome.duration_s
    result.usage = {k: v for k, v in outcome.summary()["usage"].items() if k != "total"}
    result.blocked_calls = outcome.guardrail_blocks
    result.unsafe_attempts = unsafe_attempts(outcome.trace)
    return result


def main() -> int:
    parser = argparse.ArgumentParser()
    parser.add_argument("--llm", choices=["replay", "live"], default="replay")
    parser.add_argument("--baseline", action="store_true", help="deterministic fallback only")
    parser.add_argument("--retrieval", choices=["vector", "hybrid"], default="hybrid")
    parser.add_argument("--scenarios", help="comma-separated subset")
    parser.add_argument("--date", type=date.fromisoformat, default=date.today())
    parser.add_argument("--record", action="store_true", help="live: save transcripts")
    parser.add_argument("--out", type=Path, default=None)
    args = parser.parse_args()

    settings = get_settings()
    configure_logging("ERROR")
    scenarios = load_scenarios(settings.scenarios_dir)
    selected = args.scenarios.split(",") if args.scenarios else list(scenarios)
    unknown = set(selected) - set(scenarios)
    if unknown:
        print(f"unknown scenarios: {', '.join(sorted(unknown))}")
        return 2
    embedder = make_embedder(
        settings.embedder, settings.embedding_model, settings.embedding_cache_dir
    )

    results = []
    for scenario_id in selected:
        scenario = scenarios[scenario_id]
        llm = pick_llm(args, settings, scenario_id)
        if isinstance(llm, str):
            truth = scenario.ground_truth
            results.append(ScenarioResult(scenario_id, truth.category, truth.root_entity,
                                          truth.expected_doc_ids, skipped=llm))  # fmt: skip
            print(f"{scenario_id:<16} skipped ({llm})")
            continue
        result = run_scenario(
            scenario, list(scenarios.values()), llm, settings, embedder, args.date, args.retrieval
        )
        results.append(result)
        verdict = (
            f"category {'ok ' if result.category_ok else 'BAD'} "
            f"root {'ok ' if result.root_ok else 'BAD'} violations={len(result.violations)}"
        )
        print(f"{scenario_id:<16} {result.source or '-':<8} {verdict}")

    # Leave the environment healthy: no faults, the date re-run cleanly.
    with connect(settings.admin_database_url, autocommit=True) as admin:
        reset(admin, scenarios.values(), args.date, settings.dags_dir)
    run_pg(
        settings.database_url,
        load_dag(settings.dags_dir / "ecommerce_daily.yaml"),
        args.date,
        settings.sim_seed,
    )

    mode = "baseline (fallback only)" if args.baseline else f"agent, llm={args.llm}"
    models = sorted({r.model for r in results if r.model})
    meta = {
        "generated": datetime.now(UTC).strftime("%Y-%m-%d %H:%M UTC"),
        "mode": mode,
        "model": ", ".join(models) or "-",
        "retrieval": args.retrieval,
        "logical_date": args.date.isoformat(),
    }
    out = args.out or REPORTS / ("baseline.md" if args.baseline else "latest.md")
    baseline_json = REPORTS / "baseline.json"
    baseline = None
    if not args.baseline and baseline_json.exists():
        baseline = json.loads(baseline_json.read_text())["results"]
    report = render_markdown(results, meta, baseline)
    out.parent.mkdir(parents=True, exist_ok=True)
    out.write_text(report, encoding="utf-8")
    out.with_suffix(".json").write_text(
        json.dumps(
            {
                "meta": meta,
                "summary": summarize(results),
                "results": [r.to_json() for r in results],
            },
            indent=2,
            default=str,
        )
        + "\n",
        encoding="utf-8",
    )
    print(f"\nwrote {out}\n")
    print(report)
    return 0


if __name__ == "__main__":
    sys.exit(main())
