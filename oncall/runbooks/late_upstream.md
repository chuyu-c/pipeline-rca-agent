---
title: Late or missing upstream data
category: late_upstream
tables: [raw.inventory_snapshots, raw.orders, mart.inventory_health, mart.sku_sell_through]
---
# Runbook: Late upstream data

Use this when partitions are missing or empty but no task failed. The scheduler shows green; the
symptoms are freshness alerts (`partition_missing`) and volume drops on downstream marts.

Our ingest tasks do not fail when a source file has not landed. They log a warning such as
`No file found at landing/inventory/2026-03-10.csv; source has not delivered yet. Loaded 0 rows`
and succeed, so freshness alerts are the only signal.

## Triage

### Find the first empty table in lineage order
1. List the alerted datasets and order them upstream-first. The most upstream empty partition is
   the root; the rest are downstream symptoms.
2. Check its row count for the logical date and the previous days:

```sql
SELECT logical_date, count(*) FROM raw.inventory_snapshots
WHERE logical_date >= current_date - 7
GROUP BY 1 ORDER BY 1;
```

3. Read the ingest task log for that date. A `No file found at landing/...` warning confirms the
   source has not delivered; a load with a normal row count means the problem is further down.

### Distinguish late from lost
- Late: the source usually delivers within a few hours. Check the delivery SLA in the source
  contract (inventory snapshots: 02:00 UTC, orders: 01:00 UTC).
- Lost or stopped: two or more consecutive missing days, or the source team reports an outage.

## Remediation
- If the file lands later, `rerun_task` the ingest task for that logical_date; downstream tasks
  overwrite their partitions on the next run (`backfill_partition` for each affected mart).
- If the delay is beyond the SLA, `escalate` to the source owner with the missing path and date.
- Do not quarantine downstream marts for a late feed: empty partitions are already obviously
  incomplete. Do annotate dashboards that read `mart.inventory_health`.

## Prevention
- A file-arrival sensor with an SLA alert would page before freshness monitors do.
- Treat a zero-row load as a warning-level alert on the ingest task itself.
