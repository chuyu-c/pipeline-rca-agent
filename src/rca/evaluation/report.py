"""Markdown rendering of an eval run (eval/reports/latest.md). Pure."""

from statistics import fmean
from typing import Any

from rca.evaluation.scoring import CITATION_K, TARGETS, ScenarioResult, summarize


def _pct(x: float) -> str:
    return f"{x:.0%}"


def _mark(ok: bool) -> str:
    return "✓" if ok else "✗"


def _cost(x: float | None) -> str:
    return "n/a" if x is None else f"${x:.2f}"


def render_markdown(
    results: list[ScenarioResult],
    meta: dict[str, Any],
    baseline: list[dict[str, Any]] | None = None,
) -> str:
    """`baseline`: per-scenario results of a --baseline run (its JSON `results`); lift is computed
    only over scenarios scored in both runs."""
    s = summarize(results)
    out = [
        "# Eval report",
        "",
        " · ".join(f"{k}: `{v}`" for k, v in meta.items()),
        "",
    ]
    if not s["scenarios"]:
        return "\n".join([*out, "No scenarios were scored.", ""])

    if any(r.model == "synthetic" for r in results):
        out += [
            "> **Note:** some replayed transcripts are hand-written synthetic fixtures, not live "
            "model runs. Their scores test the harness, not the model.",
            "",
        ]
    n = s["scenarios"]
    cat, root = s["category_accuracy"], s["root_accuracy"]
    rows = [
        ("RCA category accuracy", f"{_pct(cat)} ({round(cat * n)}/{n})",
         f"≥ {_pct(TARGETS['category'])}", cat >= TARGETS["category"]),
        ("Root entity accuracy", f"{_pct(root)} ({round(root * n)}/{n})",
         f"≥ {_pct(TARGETS['root'])}", root >= TARGETS["root"]),
        (f"Citation recall@{CITATION_K}", f"{s['citation_recall']:.2f}",
         f"≥ {TARGETS['recall']}", s["citation_recall"] >= TARGETS["recall"]),
        ("Alert grouping purity", f"{s['grouping_purity']:.2f}",
         f"≥ {TARGETS['purity']}", s["grouping_purity"] >= TARGETS["purity"]),
        ("Guardrail violations", str(s["violations"]), "0", s["violations"] == 0),
    ]  # fmt: skip
    out += ["## Summary", "", "| metric | value | target | met |", "|---|---|---|---|"]
    out += [f"| {name} | {value} | {target} | {_mark(ok)} |" for name, value, target, ok in rows]
    out += [
        "",
        f"Fallbacks: {s['fallbacks']}/{n} · blocked tool calls: {s['blocked_calls']} · "
        f"median steps {s['median_steps']} · median tokens {s['median_tokens']:,} · "
        f"median triage latency {s['median_latency_s']:.1f}s · "
        f"est. cost {_cost(s['total_cost_usd'])}"
        + (f" · skipped: {s['skipped']}" if s["skipped"] else ""),
        "",
        "Root entity scoring: exact match, or a column of the expected table (more specific is "
        "accepted, coarser is not); same rule for agent and baseline.",
    ]

    scored = {r.scenario: r for r in results if not r.skipped}
    paired = [b for b in baseline or [] if b["scenario"] in scored and not b.get("skipped")]
    if paired:
        out += ["", "## Lift over the deterministic baseline", "",
                f"Same {len(paired)} scenario(s) in both runs.", "",
                "| metric | this run | baseline | Δ |", "|---|---|---|---|"]  # fmt: skip
        for key, label in (
            ("category_ok", "category accuracy"),
            ("root_ok", "root entity accuracy"),
            ("citation_recall", f"citation recall@{CITATION_K}"),
        ):
            a = fmean(float(getattr(scored[b["scenario"]], key)) for b in paired)
            base = fmean(float(b[key]) for b in paired)
            out.append(f"| {label} | {a:.2f} | {base:.2f} | {a - base:+.2f} |")

    out += [
        "",
        "## Per scenario",
        "",
        "| scenario | source | category | root entity | recall@5 | incidents (purity) | steps "
        "| tokens | latency | blocked | violations |",
        "|---|---|---|---|---|---|---|---|---|---|---|",
    ]
    for r in results:
        if r.skipped:
            out.append(f"| {r.scenario} | skipped: {r.skipped} |" + " |" * 9)
            continue
        out.append(
            f"| {r.scenario} | {r.source} | {_mark(r.category_ok)} {r.category} "
            f"| {_mark(r.root_ok)} `{r.root_entity}` | {r.citation_recall:.2f} "
            f"| {len(r.alerts_per_incident)} ({r.purity:.2f}) | {r.steps} | {r.tokens:,} "
            f"| {r.latency_s:.1f}s | {r.blocked_calls} | {len(r.violations)} |"
        )

    notes = [
        r
        for r in results
        if not r.skipped
        and (not (r.category_ok and r.root_ok) or r.violations or r.fallback_reason)
    ]
    if notes:
        out += ["", "## Failure notes", ""]
        for r in notes:
            out.append(f"**{r.scenario}**")
            if not r.category_ok:
                out.append(f"- category: got `{r.category}`, expected `{r.truth_category}`")
            if not r.root_ok:
                out.append(f"- root entity: got `{r.root_entity}`, expected `{r.truth_root}`")
            if r.fallback_reason:
                out.append(f"- fallback used: {r.fallback_reason}")
            for v in r.violations:
                out.append(f"- **VIOLATION**: {v}")
            out.append(f"- summary: {r.summary[:300]}")
            out.append("")

    attempts = [(r.scenario, a) for r in results for a in r.unsafe_attempts]
    if attempts:
        out += ["", "## Blocked unsafe attempts", ""]
        out += [f"- {scenario}: {a}" for scenario, a in attempts]
    return "\n".join(out).rstrip() + "\n"
