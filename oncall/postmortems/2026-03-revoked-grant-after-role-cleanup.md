---
title: Product joins failed after role cleanup revoked SELECT
category: permission
tables: [raw.products, stg.order_items]
date: 2026-03-03
severity: SEV2
---
# Postmortem: SELECT on raw.products revoked during a role cleanup

## Summary
A quarterly access review script removed "unused" grants and revoked `SELECT` on `raw.products`
from the pipeline role `rca_app`. The next run of `stg_order_items` failed with
`InsufficientPrivilege: permission denied for table products`; its three marts were skipped.

## Impact
- Order-line staging and all product-level marts missing for one logical date.

## Investigation
- The failing task was stg_order_items, but the object in the error was `raw.products`, a static
  dimension table that no DAG task writes. The root entity was the table, not the task.
- `information_schema.role_table_grants` showed rca_app had lost SELECT; the review script's audit
  log confirmed the revoke.

## Resolution
The data platform team restored the grant through a migration; `stg_order_items` was rerun and the
marts backfilled.

## Root cause
The review script judged usage from a 30-day query log that missed reads made by a role through a
join in a single nightly task.

## Action items
- Manage pipeline grants only through migrations; exclude pipeline roles from the review script.
- Daily grant audit comparing expected and actual privileges.
