---
title: NULL region bucket in GMV by region after checkout release
category: data_quality
tables: [raw.orders, stg.orders, mart.daily_gmv_by_region]
date: 2026-01-21
severity: SEV2
---
# Postmortem: checkout release stopped sending region

## Summary
A checkout service release stopped populating `region` for orders placed through the new mobile
flow. About 38% of orders on 2026-01-20 arrived with a NULL region. All tasks succeeded;
`mart.daily_gmv_by_region` grew a NULL region row holding a third of GMV.

## Impact
- Regional GMV under-reported by roughly 38% for two days; one regional manager escalated a
  "sales collapse".

## Detection
The null-rate monitor on `raw.orders.region` (threshold 5%) fired along with the mart null-rate
monitor. The incident was opened with root candidate `raw.orders`.

## Timeline (UTC)
- 2026-01-20 16:00 checkout release ships.
- 2026-01-21 01:30 null_rate:region alerts on raw.orders and mart.daily_gmv_by_region.
- 08:00 on-call measures null rate per layer: raw already 38% null, so staging and the mart are
  propagating, not causing it.
- 09:10 null rows traced to orders from the new mobile flow; escalated to the checkout team.
- 2026-01-22 checkout fix ships; source re-sends; partitions backfilled.

## Root cause
A required field became optional in the checkout API payload with no data contract check.

## Action items
- Contract test that `region` is non-null in the orders export.
- Keep null-rate monitors at the raw layer for business-critical dimensions.
