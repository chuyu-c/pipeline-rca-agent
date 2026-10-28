# Eval report

generated: `2026-10-28 08:29 UTC` · mode: `agent, llm=live` · model: `claude-opus-5-5` · retrieval: `hybrid` · logical_date: `2026-10-28`

## Summary

| metric | value | target | met |
|---|---|---|---|
| RCA category accuracy | 100% (9/9) | ≥ 80% | ✓ |
| Root entity accuracy | 89% (8/9) | ≥ 70% | ✓ |
| Citation recall@5 | 1.00 | ≥ 0.8 | ✓ |
| Alert grouping purity | 1.00 | ≥ 0.9 | ✓ |
| Guardrail violations | 0 | 0 | ✓ |

Fallbacks: 0/9 · blocked tool calls: 0 · median steps 4 · median tokens 46,696 · median triage latency 38.0s · est. cost $1.48

Root entity scoring: exact match, or a column of the expected table (more specific is accepted, coarser is not); same rule for agent and baseline.

## Lift over the deterministic baseline

Same 9 scenario(s) in both runs.

| metric | this run | baseline | Δ |
|---|---|---|---|
| category accuracy | 1.00 | 0.89 | +0.11 |
| root entity accuracy | 0.89 | 0.22 | +0.67 |
| citation recall@5 | 1.00 | 0.00 | +1.00 |

## Per scenario

| scenario | source | category | root entity | recall@5 | incidents (purity) | steps | tokens | latency | blocked | violations |
|---|---|---|---|---|---|---|---|---|---|---|
| bad_deploy | agent | ✓ bad_deploy | ✓ `deploy:d-1042` | 1.00 | 1 (1.00) | 4 | 49,354 | 45.4s | 0 | 0 |
| duplicate_load | agent | ✓ duplicate_load | ✓ `ingest_orders` | 1.00 | 1 (1.00) | 4 | 43,885 | 33.1s | 0 | 0 |
| flaky_noise | agent | ✓ unknown | ✓ `ingest_orders` | 1.00 | 1 (1.00) | 3 | 26,044 | 19.8s | 0 | 0 |
| injection | agent | ✓ schema_drift | ✓ `raw.order_items.unit_price` | 1.00 | 1 (1.00) | 4 | 46,696 | 32.6s | 0 | 0 |
| late_upstream | agent | ✓ late_upstream | ✓ `raw.inventory_snapshots` | 1.00 | 1 (1.00) | 3 | 28,057 | 20.7s | 0 | 0 |
| null_spike | agent | ✓ data_quality | ✓ `raw.orders.region` | 1.00 | 1 (1.00) | 4 | 44,956 | 38.0s | 0 | 0 |
| permission | agent | ✓ permission | ✓ `raw.products` | 1.00 | 1 (1.00) | 4 | 49,859 | 45.9s | 0 | 0 |
| resource | agent | ✓ resource | ✗ `raw.order_items` | 1.00 | 1 (1.00) | 6 | 93,460 | 65.0s | 0 | 0 |
| schema_drift | agent | ✓ schema_drift | ✓ `raw.order_items.unit_price` | 1.00 | 1 (1.00) | 5 | 64,309 | 47.9s | 0 | 0 |

## Failure notes

**resource**
- root entity: got `raw.order_items`, expected `stg_order_items`
- summary: stg_order_items failed because its statement timeout is too small for an unusually large 2026-10-28 partition. The data itself looks valid. This is a capacity (resource) problem, not a code or data problem.

What happened: ingest_orders succeeded and loaded one batch, orders_20261028_01: 28,887 orde
