---
title: Bad deploy to a pipeline task
category: bad_deploy
tables: [stg.orders, stg.order_items]
---
# Runbook: Bad deploy

Use this when a pipeline change shipped shortly before the run that broke. Bad deploys show up
either as failures (syntax, missing column introduced by the diff) or — worse — as silent
correctness bugs: row explosions from a join on a non-unique key, filters dropped, wrong grain.

## Triage

### Correlate in time
1. Query recent deploys for the alerted tasks and their upstream tasks:

```sql
SELECT deploy_id, ts, author, task_id, commit_sha, message
FROM ops.deploys
WHERE ts >= now() - interval '24 hours'
ORDER BY ts DESC;
```

2. A deploy to the task that writes the first anomalous table, minutes before the run, is the prime
   suspect. The executor logs the deployed version: `Executing sql/stg/order_items.sql (deploy
   d-1042, commit 9f3c2ab)`.

### Read the diff for fan-out
- Joins on non-unique keys are the most common silent bug. `raw.products` is unique on
  `product_id` only; joining on `category`, `name` or `sku` prefixes multiplies rows.
- Check the grain: rows per `(order_id, line_no)` in `stg.order_items` must be exactly 1.

```sql
SELECT count(*) AS rows, count(DISTINCT (order_id, line_no)) AS lines
FROM stg.order_items WHERE logical_date = DATE '2026-03-10';
```

## Remediation
- `revert_deploy` for the offending deploy id. Do not hot-fix forward during an incident.
- After the revert, `rerun_task` the task and `backfill_partition` downstream for every date the
  bad version ran.
- `quarantine_partition` marts that finance reads until they are rebuilt.

## Prevention
- Add a uniqueness test on the stg grain to CI.
- Require a row-count comparison against the previous version for SQL changes to staging.
