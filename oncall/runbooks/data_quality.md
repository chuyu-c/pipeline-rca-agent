---
title: Data quality anomalies (nulls, invalid values)
category: data_quality
tables: [raw.orders, stg.orders, mart.daily_gmv_by_region]
---
# Runbook: Data quality anomalies

Use this when tasks succeed but values are wrong: a null-rate monitor fires
(`null_rate:region`), a NULL bucket appears in a mart, or values fall outside expected ranges.
Nothing errors, so the logs will look clean.

## Triage

### Locate where the bad values first appear
Walk lineage upstream from the alerted mart and measure the same column at each layer for the
logical date:

```sql
SELECT logical_date,
       count(*) FILTER (WHERE region IS NULL)::numeric / count(*) AS null_rate
FROM raw.orders
WHERE logical_date >= current_date - 7
GROUP BY 1 ORDER BY 1;
```

If `raw.orders.region` is already null at the same rate, the source sent it that way; staging and
marts are faithfully propagating it. If raw is clean and stg is not, suspect the stg SQL or a
recent deploy.

### Characterize the bad rows
- Is the null rate sudden (one day) or gradual?
- Do the bad rows share an attribute: order_ts window, customer cohort, status, channel?
- A sharp step on one date usually means an upstream release (checkout, app version) changed what
  is sent.

## Remediation
- Raw data is the system of record; do not patch values in raw.
- `quarantine_partition` the affected mart partition if downstream consumers (finance GMV report)
  would act on it, and annotate the dashboard.
- `escalate` to the source team with the column, rate, first bad date and example order_ids.
- Once the source fixes and re-sends, `rerun_task` ingest and `backfill_partition` downstream.

## Prevention
- Keep null-rate thresholds on business-critical dimensions (region, status) at the raw layer, not
  only on marts — earlier detection, clearer ownership.
