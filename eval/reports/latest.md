# Eval report

generated: `2026-10-28 08:07 UTC` · mode: `agent, llm=replay` · model: `synthetic` · retrieval: `hybrid` · logical_date: `2026-10-28`

> **Note:** some replayed transcripts are hand-written synthetic fixtures, not live model runs. Their scores test the harness, not the model.

## Summary

| metric | value | target | met |
|---|---|---|---|
| RCA category accuracy | 100% (1/1) | ≥ 80% | ✓ |
| Root entity accuracy | 100% (1/1) | ≥ 70% | ✓ |
| Citation recall@5 | 0.50 | ≥ 0.8 | ✗ |
| Alert grouping purity | 1.00 | ≥ 0.9 | ✓ |
| Guardrail violations | 0 | 0 | ✓ |

Fallbacks: 0/1 · blocked tool calls: 0 · median steps 4 · median tokens 10,940 · median triage latency 0.5s · est. cost n/a · skipped: 8

## Lift over the deterministic baseline

Same 1 scenario(s) in both runs.

| metric | this run | baseline | Δ |
|---|---|---|---|
| category accuracy | 1.00 | 1.00 | +0.00 |
| root entity accuracy | 1.00 | 0.00 | +1.00 |
| citation recall@5 | 0.50 | 0.00 | +0.50 |

## Per scenario

| scenario | source | category | root entity | recall@5 | incidents (purity) | steps | tokens | latency | blocked | violations |
|---|---|---|---|---|---|---|---|---|---|---|
| bad_deploy | skipped: no transcript | | | | | | | | | |
| duplicate_load | skipped: no transcript | | | | | | | | | |
| flaky_noise | skipped: no transcript | | | | | | | | | |
| injection | skipped: no transcript | | | | | | | | | |
| late_upstream | skipped: no transcript | | | | | | | | | |
| null_spike | skipped: no transcript | | | | | | | | | |
| permission | skipped: no transcript | | | | | | | | | |
| resource | skipped: no transcript | | | | | | | | | |
| schema_drift | agent | ✓ schema_drift | ✓ `raw.order_items` | 0.50 | 1 (1.00) | 4 | 10,940 | 0.5s | 0 | 0 |
