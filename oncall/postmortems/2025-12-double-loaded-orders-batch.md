---
title: GMV doubled after orders batch was re-sent
category: duplicate_load
tables: [raw.orders, raw.order_items, stg.orders, stg.order_items, mart.daily_gmv_by_region]
date: 2025-12-02
severity: SEV2
---
# Postmortem: orders batch loaded twice

## Summary
During `ingest_orders` an upload acknowledgement timed out and the loader re-sent the whole batch
(`Upload acknowledgement for batch orders_20251201_01 timed out after 30s; re-sending batch`). Both
copies were committed. Row counts and GMV for 2025-12-01 doubled. No task failed.

## Impact
- Finance's daily GMV report showed a record day (+98%) and was forwarded to leadership before the
  correction.

## Detection
`volume_high` alerts on raw.orders, raw.order_items, stg.orders and stg.order_items (z-scores
above 8).

## Investigation
- `count(*)` was exactly twice `count(DISTINCT order_id)` for batch `orders_20251201_01`, with two
  `_loaded_at` clusters 31 seconds apart.
- The ingest log showed the re-send warning inside a single successful attempt.

## Root cause
The loader's retry path appended instead of replacing; raw tables have no primary key, so nothing
rejected the duplicates. The producing task, `ingest_orders`, was the root entity.

## Resolution
Reran `ingest_orders` for 2025-12-01 (it deletes the partition first), then backfilled stg and marts.

## Action items
- Make the upload retry idempotent per `_batch_id`.
- Monitor duplicate order_ids in raw.orders.
