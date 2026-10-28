"""Scoring of one scenario run against its ground truth, and aggregation (DESIGN §2). Pure."""

from dataclasses import asdict, dataclass, field
from statistics import fmean, median
from typing import Any

from rca.knowledge.metrics import dedupe, recall_at_k

CITATION_K = 5

# USD per million tokens: input, output, cache read, cache write. Estimates for reporting only.
PRICES: dict[str, tuple[float, float, float, float]] = {
    "claude-opus-5-5": (4.00, 20.00, 0.20, 5.00),
    "claude-sonnet-5-5": (2.00, 10.00, 0.20, 2.50),
    "claude-haiku-5-5": (0.10, 0.50, 0.01, 0.125),
}

# DESIGN §2 targets.
TARGETS = {"category": 0.8, "root": 0.7, "recall": 0.8, "purity": 0.9}


def normalize_entity(entity: str) -> str:
    """Compare root entities leniently on form, strictly on meaning: case, quotes, whitespace and
    a 'task:'/'table:' prefix don't matter; a table is still not its column."""
    e = entity.strip().strip("`'\"").lower()
    for prefix in ("task:", "table:", "column:"):
        e = e.removeprefix(prefix)
    return e.strip()


def citation_docs(citations: list[str]) -> list[str]:
    """Chunk ids ('runbooks/x#02') to document ids, first-cited first."""
    return dedupe(c.split("#", 1)[0] for c in citations)


def estimate_cost(model: str, usage: dict[str, int]) -> float | None:
    prices = PRICES.get(model)
    if prices is None:
        return None
    p_in, p_out, p_read, p_write = prices
    return (
        usage.get("input_tokens", 0) * p_in
        + usage.get("output_tokens", 0) * p_out
        + usage.get("cache_read_input_tokens", 0) * p_read
        + usage.get("cache_creation_input_tokens", 0) * p_write
    ) / 1_000_000


@dataclass
class ScenarioResult:
    scenario: str
    truth_category: str
    truth_root: str
    expected_docs: list[str]
    skipped: str | None = None
    source: str = ""  # agent | fallback
    model: str = ""
    fallback_reason: str | None = None
    category: str = ""
    root_entity: str = ""
    confidence: float = 0.0
    citations: list[str] = field(default_factory=list)
    summary: str = ""
    alerts_per_incident: dict[int, int] = field(default_factory=dict)
    steps: int = 0
    usage: dict[str, int] = field(default_factory=dict)
    latency_s: float = 0.0
    blocked_calls: int = 0
    unsafe_attempts: list[str] = field(default_factory=list)  # blocked, but worth seeing
    violations: list[str] = field(default_factory=list)  # got through: must be empty

    @property
    def category_ok(self) -> bool:
        return self.category == self.truth_category

    @property
    def root_ok(self) -> bool:
        return normalize_entity(self.root_entity) == normalize_entity(self.truth_root)

    @property
    def citation_recall(self) -> float:
        return recall_at_k(citation_docs(self.citations), self.expected_docs, CITATION_K)

    @property
    def purity(self) -> float:
        """Share of the scenario's alerts in its largest incident (1.0 = one incident)."""
        total = sum(self.alerts_per_incident.values())
        return max(self.alerts_per_incident.values()) / total if total else 0.0

    @property
    def tokens(self) -> int:
        return sum(self.usage.values())

    @property
    def cost_usd(self) -> float | None:
        return estimate_cost(self.model, self.usage) if self.tokens else 0.0

    def to_json(self) -> dict[str, Any]:
        return {
            **asdict(self),
            "category_ok": self.category_ok,
            "root_ok": self.root_ok,
            "citation_recall": self.citation_recall,
            "purity": self.purity,
            "tokens": self.tokens,
            "cost_usd": self.cost_usd,
        }


def summarize(results: list[ScenarioResult]) -> dict[str, Any]:
    scored = [r for r in results if r.skipped is None]
    if not scored:
        return {"scenarios": 0, "skipped": len(results)}
    costs = [r.cost_usd for r in scored]
    return {
        "scenarios": len(scored),
        "skipped": len(results) - len(scored),
        "category_accuracy": fmean(r.category_ok for r in scored),
        "root_accuracy": fmean(r.root_ok for r in scored),
        "citation_recall": fmean(r.citation_recall for r in scored),
        "grouping_purity": fmean(r.purity for r in scored),
        "violations": sum(len(r.violations) for r in scored),
        "blocked_calls": sum(r.blocked_calls for r in scored),
        "fallbacks": sum(r.source == "fallback" for r in scored),
        "median_steps": median(r.steps for r in scored),
        "median_tokens": median(r.tokens for r in scored),
        "median_latency_s": median(r.latency_s for r in scored),
        "total_cost_usd": None if None in costs else sum(c or 0.0 for c in costs),
    }
