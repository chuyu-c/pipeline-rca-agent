---
title: Inventory health dashboard empty after late inventory feed
category: late_upstream
tables: [raw.inventory_snapshots, mart.inventory_health, mart.sku_sell_through]
date: 2025-11-04
severity: SEV3
---
# Postmortem: inventory snapshot feed delivered nine hours late

## Summary
The warehouse system's nightly inventory export failed to publish. `ingest_inventory` found no file
(`No file found at landing/inventory/2025-11-04.csv; source has not delivered yet. Loaded 0 rows`),
succeeded with zero rows, and every downstream task ran on an empty partition. No task failed.

## Impact
- `mart.inventory_health` and `mart.sku_sell_through` had no rows for 2025-11-04 until 12:30 UTC.
- The replenishment team planned orders from the previous day's numbers.

## Timeline (UTC)
- 02:00 expected file arrival; nothing lands.
- 02:15 ingest_inventory succeeds with 0 rows.
- 03:00 freshness alerts on raw.inventory_snapshots and two marts; grouped into one incident.
- 06:30 on-call confirms the missing file and escalates to the warehouse systems team.
- 11:50 file lands; ingest rerun, marts backfilled by 12:30.

## Root cause
An expired credential on the warehouse system's export job. Our side behaved as designed, but a
zero-row load produced no alert of its own; the first signal was downstream freshness.

## Action items
- Add a file-arrival SLA sensor for the inventory feed.
- Treat zero-row ingest loads as warnings on the ingest task.
