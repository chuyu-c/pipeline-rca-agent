---
title: Order item prices missing after source renamed unit_price
category: schema_drift
tables: [raw.order_items, stg.order_items, mart.daily_gmv_by_region]
date: 2025-09-16
severity: SEV2
---
# Postmortem: source renamed unit_price to price

## Summary
The order export renamed `unit_price` to `price` in a schema update. Our loader auto-evolved
`raw.order_items`, so ingest kept succeeding, but `stg_order_items` failed with
`UndefinedColumn: column i.unit_price does not exist`. GMV, sell-through and inventory-health marts
were stale for two logical dates.

## Impact
- `mart.daily_gmv_by_region` missing 2025-09-15 and 2025-09-16; the daily finance email showed
  zeros.
- `mart.sku_sell_through` and `mart.inventory_health` stale for the same dates.

## Timeline (UTC)
- 01:10 ingest_orders loads the new export; `raw.order_items` now has `price`.
- 01:25 stg_order_items fails twice with UndefinedColumn; three marts marked upstream_failed.
- 07:40 on-call starts from the freshness alerts on the marts and spends an hour on the mart SQL.
- 08:55 on-call compares raw columns against the stg SQL and finds the rename.
- 11:30 source team confirms the rename is permanent; stg SQL updated, two dates backfilled.

## Root cause
An upstream contract change (column rename) with no notice, and a loader that silently evolves raw
schemas. The failing task was downstream of the actual change.

## What went well / poorly
- Correlated alerts were grouped, but on-call investigated the marts first instead of the most
  upstream failure.

## Action items
- Contract test on raw.order_items columns before staging runs.
- Alert on information_schema changes in the raw schema.
