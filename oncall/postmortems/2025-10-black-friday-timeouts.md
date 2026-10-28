---
title: stg_order_items timed out on an oversized sale-day partition
category: resource
tables: [raw.order_items, raw.orders, stg.order_items]
date: 2025-10-28
severity: SEV2
---
# Postmortem: statement timeouts on a sale-day partition

## Summary
A flash sale plus a bulk backfill of delayed marketplace orders landed in a single logical date.
`raw.order_items` for 2025-10-27 was about 100x a normal day. `stg_order_items` ran with its usual
tight statement timeout and failed on every attempt with
`QueryCanceled: canceling statement due to statement timeout`. The three marts downstream were
skipped.

## Impact
- GMV, sell-through and inventory-health marts were missing 2025-10-27 for six hours during the
  busiest reporting day of the quarter.
- Finance delayed the morning sales update.

## Detection
Two signals arrived together and were grouped into one incident:
- `task_failure` on stg.order_items with error class QueryCanceled (two attempts, same duration).
- `volume_high` on raw.orders and raw.order_items with z-scores above 50.

## Timeline (UTC)
- 00:50 marketplace backfill and sale-day orders land through ingest_orders; ingest succeeds.
- 01:05 stg_order_items attempt 1 hits the statement timeout after the configured limit.
- 01:07 attempt 2 fails identically; task marked failed; marts upstream_failed.
- 01:20 freshness alerts on the marts join the same incident.
- 06:10 on-call first suspects lock contention and spends forty minutes on pg_stat_activity.
- 06:50 on-call compares partition sizes with the previous week and finds the 100x input.
- 07:05 data platform approves a temporary timeout increase for stg_order_items.
- 07:20 rerun succeeds; marts backfilled by 07:40.

## Root cause
The task's statement timeout was sized for a typical day. Retries used the same limit, so the
failure was deterministic, not transient. The large partition itself was legitimate data, although
the marketplace backfill should have been spread across its original dates.

## Investigation notes
- Identical durations on both attempts are the tell: transient database problems produce varying
  durations and different errors.
- Volume alerts on raw tables in the same incident pointed at input size from the start; reading
  them first would have saved about an hour.
- No deploy or configuration change preceded the incident, which ruled out a lowered timeout.

## What went well
- Alert correlation put the task failure and the raw volume alerts in one incident.
- The fix needed no code change.

## What went poorly
- On-call investigated database health before input size.
- The timeout was not documented anywhere on-call could see it.

## Action items
- Size task timeouts from p99 runtime plus headroom; review quarterly before sale periods.
- Route marketplace backfills to their original logical dates.
- Add partition-size context to task_failure alerts so the input size is visible at a glance.
- Document per-task timeouts next to the DAG definition.
