---
title: GMV by region shifted between days after timezone change
category: data_quality
tables: [mart.daily_gmv_by_region, stg.orders]
date: 2025-08-12
severity: SEV3
---
# Postmortem: GMV attributed to the wrong day after a timezone change

## Summary
A BI-layer change started converting `order_ts` to local time before grouping, while
`mart.daily_gmv_by_region` groups by `logical_date` in UTC. Dashboards showed GMV moving between
adjacent days for the west region. The warehouse data was correct.

## Impact
- Confusing day-over-day comparisons for one week; no data was wrong in the warehouse.

## Detection
Reported by an analyst; no monitor fired because row counts and null rates were normal.

## Root cause
Inconsistent timezone handling between the dashboard and the mart definition.

## Resolution
Dashboard reverted to UTC logical dates and labelled accordingly.

## Action items
- Document that all marts use UTC `logical_date`.
