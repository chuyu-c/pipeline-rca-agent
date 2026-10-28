from rca.evaluation.report import render_markdown
from tests.unit.evaluation.test_scoring import result

META = {"mode": "agent, llm=replay", "retrieval": "hybrid"}


def test_report_has_summary_targets_and_per_scenario_rows() -> None:
    md = render_markdown(
        [
            result(),
            result(scenario="bad_deploy", truth_category="bad_deploy",
                   category="duplicate_load", truth_root="deploy:d-1042",
                   root_entity="stg.order_items", source="fallback",
                   fallback_reason="step budget exhausted (12 turns)"),
            result(scenario="resource", skipped="no transcript"),
        ],
        META,
    )  # fmt: skip
    assert md.startswith("# Eval report")
    assert "mode: `agent, llm=replay`" in md
    assert "| RCA category accuracy | 50% (1/2) | ≥ 80% | ✗ |" in md
    assert "| Guardrail violations | 0 | 0 | ✓ |" in md
    assert "| schema_drift | agent | ✓ schema_drift | ✓ `raw.order_items` |" in md
    assert "| resource | skipped: no transcript |" in md
    assert "**bad_deploy**" in md and "expected `deploy:d-1042`" in md
    assert "fallback used: step budget exhausted" in md
    assert "Lift over" not in md  # no baseline given


def test_violations_and_unsafe_attempts_are_prominent() -> None:
    md = render_markdown(
        [result(violations=["rows:raw.orders changed: '300' -> '0'"],
                unsafe_attempts=["run_readonly_sql (not_select): DROP TABLE x"])],
        META,
    )  # fmt: skip
    assert "| Guardrail violations | 1 | 0 | ✗ |" in md
    assert "**VIOLATION**: rows:raw.orders changed" in md
    assert "## Blocked unsafe attempts" in md


def test_lift_is_computed_on_the_same_scenarios_only() -> None:
    baseline = [
        {"scenario": "schema_drift", "category_ok": True, "root_ok": False, "citation_recall": 0.0},
        {"scenario": "late_upstream", "category_ok": True, "root_ok": True, "citation_recall": 0.0},
    ]
    md = render_markdown([result(), result(scenario="permission", skipped="no transcript")],
                         META, baseline)  # fmt: skip
    assert "Same 1 scenario(s) in both runs." in md
    assert "| root entity accuracy | 1.00 | 0.00 | +1.00 |" in md


def test_synthetic_transcripts_are_called_out() -> None:
    assert "hand-written synthetic" in render_markdown([result(model="synthetic")], META)
    assert "hand-written synthetic" not in render_markdown([result()], META)
