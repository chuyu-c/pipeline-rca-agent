---
title: Schema drift in raw or staging tables
category: schema_drift
tables: [raw.orders, raw.order_items, raw.inventory_snapshots, stg.orders, stg.order_items]
---
# Runbook: Schema drift

Use this when a staging or mart task fails because a column it reads no longer exists, changed type,
or appeared under a new name. Typical errors in `ops.task_logs`:

- `UndefinedColumn: column i.unit_price does not exist`
- `UndefinedColumn: column "region" does not exist`
- `DatatypeMismatch: column "quantity" is of type text but expression is of type integer`

The failing task is almost never the cause. The cause is upstream: a source system renamed or retyped
a field and the loader auto-evolved the raw table to match.

## Triage

### Confirm it is drift, not a bad deploy
1. Read the error line of the failed attempt: which column, which alias, which SQL file.
2. Check `ops.deploys` for the failing task in the last 24 hours. If the SQL file changed and the
   column was removed *in the diff*, this is a bad deploy — use the bad_deploy runbook.
3. If the SQL did not change, compare the raw table's current columns with what the SQL expects:

```sql
SELECT column_name, data_type
FROM information_schema.columns
WHERE table_schema = 'raw' AND table_name = 'order_items'
ORDER BY ordinal_position;
```

A column with a similar name (`price` instead of `unit_price`, `region_code` instead of `region`)
is the signature of an upstream rename.

### Scope the blast radius
- Everything downstream of the failing stg task is `upstream_failed` and its partitions go stale.
  For `stg.order_items` that is `mart.daily_gmv_by_region`, `mart.sku_sell_through` and
  `mart.inventory_health`.
- Freshness alerts on those marts are symptoms of the same incident. Do not open separate tickets.

## Remediation

### Short term
- Do **not** hand-edit the stg SQL during the incident to read the new column name; that hides the
  contract break and the source may rename it back.
- Escalate to the source owning team with the old and new column names and the first logical_date
  affected. Ask whether the rename is permanent.
- If the source confirms the rename is permanent, ship a reviewed change to the stg SQL, then
  `rerun_task` for the stg task and let downstream marts run.

### Backfill
Rerunning the stg task for each affected logical_date rebuilds the marts. Use `backfill_partition`
for every date between the first failure and the fix.

## Prevention
- Add a contract test on raw tables used by staging (expected columns and types).
- Alert on raw schema changes (information_schema diff) before the next staging run.
