---
title: Resource exhaustion and statement timeouts
category: resource
tables: [stg.order_items, stg.orders, raw.order_items]
---
# Runbook: Resource exhaustion and timeouts

Use this when tasks fail with `QueryCanceled: canceling statement due to statement timeout`, run
out of memory or disk, or retry repeatedly with the same timeout. Each task runs with its own
`statement_timeout`; retries use the same limit, so a deterministic timeout fails every attempt.

## Triage

### Is the input unusually large?
Compare the input partition with recent days before blaming the database:

```sql
SELECT logical_date, count(*) FROM raw.order_items
WHERE logical_date >= current_date - 7
GROUP BY 1 ORDER BY 1;
```

A partition many times larger than normal (sale event, bulk backfill landing in one day) explains
the timeout; the fix is capacity, not code. Volume alerts on the raw table usually accompany it.

### Or is the limit unusually small?
- Check whether the task's timeout was recently lowered (deploy or config change).
- Check for lock waits: another long transaction on the same table.

### Retry pattern
`up_for_retry` then `failed` with the same error and similar duration on each attempt means the
failure is deterministic. Transient infra errors look different (connection resets, varying
durations) — see the general on-call runbook.

## Remediation
- If the volume is legitimate, raise the task's timeout for the run (with the data platform team's
  approval) and `rerun_task`. Then `backfill_partition` downstream.
- If the volume is not legitimate (bad backfill, duplicate load), fix the input first — see the
  duplicate_load runbook.
- `escalate` to the data platform on-call for database capacity problems (disk, connections).

## Prevention
- Size timeouts from p99 runtime with headroom, not from typical days.
- Split very large partitions into hourly batches.
