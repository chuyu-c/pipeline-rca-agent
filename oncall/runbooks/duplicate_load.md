---
title: Duplicate loads and double counting
category: duplicate_load
tables: [raw.orders, raw.order_items, stg.orders, stg.order_items, mart.daily_gmv_by_region]
---
# Runbook: Duplicate loads

Use this when volumes jump to roughly 2x with no task failure: `volume_high` alerts on raw and stg
tables, GMV doubling in `mart.daily_gmv_by_region`, finance asking why yesterday looks like a record
day.

Raw and staging tables have no primary keys on purpose (they accept whatever ingest writes), so a
batch that is written twice is stored twice.

## Triage

### Confirm duplication rather than real growth
```sql
SELECT _batch_id, count(*) AS rows, count(DISTINCT order_id) AS distinct_orders,
       min(_loaded_at), max(_loaded_at)
FROM raw.orders
WHERE logical_date = DATE '2026-03-10'
GROUP BY 1;
```

`rows = 2 x distinct_orders` for one `_batch_id`, with two clusters of `_loaded_at`, is a replayed
batch. Real growth shows more distinct order_ids, not repeated ones.

### Find the writer
- The table's producing task is the root entity, not the marts. For raw orders that is
  `ingest_orders`.
- Read its log for the date. Look for retries inside a single attempt, e.g.
  `Upload acknowledgement for batch orders_20260310_01 timed out after 30s; re-sending batch`.
  An upload retry without idempotency is the classic cause.
- Also check whether the ingest task was manually re-triggered in append mode.

## Remediation
- `rerun_task` the ingest task for the date: our ingest deletes the partition before loading, so a
  clean rerun replaces the doubled rows.
- Then `backfill_partition` for every downstream table of that date (stg and all marts).
- If a finance extract already went out, `escalate` so the report can be reissued.

## Prevention
- Make batch loads idempotent: delete-then-insert per `_batch_id` inside one transaction, or upsert
  on a natural key.
- Alert on `count(*) != count(DISTINCT order_id)` in raw.orders.
