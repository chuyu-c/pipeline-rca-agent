---
title: Test orders leaked into staging after stg_orders deploy
category: bad_deploy
tables: [stg.orders, mart.daily_gmv_by_region]
date: 2025-06-18
severity: SEV3
---
# Postmortem: stg_orders deploy dropped the test-order filter

## Summary
A cleanup deploy to `stg_orders` removed a `WHERE` clause that excluded internal test orders.
About 2% extra orders reached `stg.orders` and GMV for one day.

## Impact
- Small GMV overstatement for one day, caught by finance reconciliation.

## Detection
No monitor fired; the change was within normal volume variance.

## Root cause
Refactor removed a filter whose purpose was undocumented.

## Resolution
Deploy reverted; stg_orders rerun; mart backfilled.

## Action items
- Comment business filters in staging SQL with their reason.
- Reconcile mart GMV with the source system daily.
