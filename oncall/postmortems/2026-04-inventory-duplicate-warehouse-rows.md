---
title: Inventory health overstated stock after warehouse id remap
category: duplicate_load
tables: [raw.inventory_snapshots, mart.inventory_health]
date: 2026-04-07
severity: SEV3
---
# Postmortem: duplicate warehouse rows in inventory snapshots

## Summary
The warehouse system briefly published snapshots under both the old and new id for one warehouse
(`wh-east` and `wh-east-2`). `mart.inventory_health` summed both, overstating on-hand stock and days
of cover for every SKU in that warehouse.

## Impact
- Low-stock flags suppressed for one day; two SKUs stocked out without a replenishment order.

## Detection
`volume_high` on raw.inventory_snapshots (rows +50%). Orders tables were unaffected.

## Root cause
A source-side id migration ran both ids in parallel without notice. Not a re-sent batch: the
duplicate rows carried different `warehouse_id` values.

## Resolution
Source dropped the old id; ingest_inventory rerun; mart backfilled.

## Action items
- Validate warehouse ids against the known list during ingest.
