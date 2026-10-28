---
title: Order lines multiplied after stg_order_items join change
category: bad_deploy
tables: [stg.order_items, mart.daily_gmv_by_region, mart.sku_sell_through]
date: 2026-02-11
severity: SEV1
---
# Postmortem: fan-out join deployed to stg_order_items

## Summary
A deploy to `stg_order_items` changed the products join from `product_id` to a key that is not
unique in `raw.products`. Every order line matched several products. `stg.order_items` grew about
8x and GMV was inflated by the same factor. The run succeeded.

## Impact
- GMV, sell-through and inventory-health marts wrong for one day; sell-through rates above 1.0.

## Detection
`volume_high` on stg.order_items only (row count about 8x the trailing mean). Mart row counts did
not change because they aggregate, so mart volume monitors stayed quiet.

## Timeline (UTC)
- 00:40 deploy to stg_order_items ("join products via category for the new taxonomy").
- 01:20 run executes the deployed SQL (`Executing sql/stg/order_items.sql (deploy ..., commit ...)`).
- 01:35 volume alert on stg.order_items.
- 02:10 on-call lists deploys from the last 24h and reads the diff: join on `p.category`.
- 02:25 deploy reverted, stg_order_items rerun, marts backfilled.

## Root cause
A join on a non-unique key passed review because the change looked like a refactor. No test
asserted one row per `(order_id, line_no)`.

## Action items
- Uniqueness test on the stg.order_items grain in CI.
- Row-count diff against the previous version for staging SQL changes.
