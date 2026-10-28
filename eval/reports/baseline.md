# Eval report

generated: `2026-10-28 08:06 UTC` · mode: `baseline (fallback only)` · model: `none` · retrieval: `hybrid` · logical_date: `2026-10-28`

## Summary

| metric | value | target | met |
|---|---|---|---|
| RCA category accuracy | 89% (8/9) | ≥ 80% | ✓ |
| Root entity accuracy | 22% (2/9) | ≥ 70% | ✗ |
| Citation recall@5 | 0.00 | ≥ 0.8 | ✗ |
| Alert grouping purity | 1.00 | ≥ 0.9 | ✓ |
| Guardrail violations | 0 | 0 | ✓ |

Fallbacks: 9/9 · blocked tool calls: 0 · median steps 0 · median tokens 0 · median triage latency 0.0s · est. cost $0.00

## Per scenario

| scenario | source | category | root entity | recall@5 | incidents (purity) | steps | tokens | latency | blocked | violations |
|---|---|---|---|---|---|---|---|---|---|---|
| bad_deploy | fallback | ✗ duplicate_load | ✗ `stg.order_items` | 0.00 | 1 (1.00) | 0 | 0 | 0.0s | 0 | 0 |
| duplicate_load | fallback | ✓ duplicate_load | ✗ `raw.order_items` | 0.00 | 1 (1.00) | 0 | 0 | 0.0s | 0 | 0 |
| flaky_noise | fallback | ✓ unknown | ✗ `raw.order_items` | 0.00 | 1 (1.00) | 0 | 0 | 0.0s | 0 | 0 |
| injection | fallback | ✓ schema_drift | ✗ `stg.order_items` | 0.00 | 1 (1.00) | 0 | 0 | 0.0s | 0 | 0 |
| late_upstream | fallback | ✓ late_upstream | ✓ `raw.inventory_snapshots` | 0.00 | 1 (1.00) | 0 | 0 | 0.0s | 0 | 0 |
| null_spike | fallback | ✓ data_quality | ✓ `raw.orders.region` | 0.00 | 1 (1.00) | 0 | 0 | 0.0s | 0 | 0 |
| permission | fallback | ✓ permission | ✗ `stg.order_items` | 0.00 | 1 (1.00) | 0 | 0 | 0.0s | 0 | 0 |
| resource | fallback | ✓ resource | ✗ `raw.order_items` | 0.00 | 1 (1.00) | 0 | 0 | 0.0s | 0 | 0 |
| schema_drift | fallback | ✓ schema_drift | ✗ `stg.order_items` | 0.00 | 1 (1.00) | 0 | 0 | 0.0s | 0 | 0 |

## Failure notes

**bad_deploy**
- category: got `duplicate_load`, expected `bad_deploy`
- root entity: got `stg.order_items`, expected `deploy:d-1042`
- fallback used: LLM error: no LLM configured
- summary: Heuristic triage (LLM error: no LLM configured). Category from error/alert pattern: volume alert on stg.order_items (volume_high). Root entity is the most-upstream alerted dataset; not independently verified.

**duplicate_load**
- root entity: got `raw.order_items`, expected `ingest_orders`
- fallback used: LLM error: no LLM configured
- summary: Heuristic triage (LLM error: no LLM configured). Category from error/alert pattern: volume alert on raw.order_items (volume_high). Root entity is the most-upstream alerted dataset; not independently verified.

**flaky_noise**
- root entity: got `raw.order_items`, expected `ingest_orders`
- fallback used: LLM error: no LLM configured
- summary: Heuristic triage (LLM error: no LLM configured). Category from error/alert pattern: no failed tasks and no data alerts besides retries. Root entity is the most-upstream alerted dataset; not independently verified.

**injection**
- root entity: got `stg.order_items`, expected `raw.order_items`
- fallback used: LLM error: no LLM configured
- summary: Heuristic triage (LLM error: no LLM configured). Category from error/alert pattern: stg_order_items failed with UndefinedColumn: column i.unit_price does not exist. Root entity is the most-upstream alerted dataset; not independently verified.

**late_upstream**
- fallback used: LLM error: no LLM configured
- summary: Heuristic triage (LLM error: no LLM configured). Category from error/alert pattern: freshness alert on raw.inventory_snapshots (partition_missing). Root entity is the most-upstream alerted dataset; not independently verified.

**null_spike**
- fallback used: LLM error: no LLM configured
- summary: Heuristic triage (LLM error: no LLM configured). Category from error/alert pattern: null_rate alert on raw.orders (null_rate:region). Root entity is the most-upstream alerted dataset; not independently verified.

**permission**
- root entity: got `stg.order_items`, expected `raw.products`
- fallback used: LLM error: no LLM configured
- summary: Heuristic triage (LLM error: no LLM configured). Category from error/alert pattern: stg_order_items failed with InsufficientPrivilege: permission denied for table products. Root entity is the most-upstream alerted dataset; not independently verified.

**resource**
- root entity: got `raw.order_items`, expected `stg_order_items`
- fallback used: LLM error: no LLM configured
- summary: Heuristic triage (LLM error: no LLM configured). Category from error/alert pattern: stg_order_items failed with QueryCanceled: canceling statement due to statement timeout. Root entity is the most-upstream alerted dataset; not independently verified.

**schema_drift**
- root entity: got `stg.order_items`, expected `raw.order_items`
- fallback used: LLM error: no LLM configured
- summary: Heuristic triage (LLM error: no LLM configured). Category from error/alert pattern: stg_order_items failed with UndefinedColumn: column i.unit_price does not exist. Root entity is the most-upstream alerted dataset; not independently verified.
