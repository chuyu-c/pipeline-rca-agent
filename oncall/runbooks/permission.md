---
title: Permission and grant failures
category: permission
tables: [raw.products, raw.orders, raw.order_items]
---
# Runbook: Permission failures

Use this when a task fails with `InsufficientPrivilege: permission denied for table products` (or
schema, or sequence). The pipeline runs as the `rca_app` role; it needs SELECT on everything it
reads and INSERT/DELETE on what it writes.

## Triage

### Identify the object and the role
1. The error names the object: `permission denied for table products` means `raw.products`, even
   though the failing task is `stg_order_items`. The table in the error is the root entity.
2. Confirm the current grants:

```sql
SELECT grantee, privilege_type
FROM information_schema.role_table_grants
WHERE table_schema = 'raw' AND table_name = 'products';
```

3. Ask what changed: role cleanups, a migration that recreated the table (grants are dropped with
   it), or a new table created by a role without default privileges.

### Blast radius
Every task reading the object fails or is skipped. `raw.products` feeds `stg.order_items`,
`mart.sku_sell_through` and `mart.inventory_health`.

## Remediation
- Grants are owned by the data platform team: `escalate` with the exact object, role and privilege.
  Do not grant broader access (e.g. a whole schema) to unblock.
- Once restored, `rerun_task` the failed task; downstream tasks run on the next schedule or via
  `backfill_partition`.

## Prevention
- Manage grants in migrations, never by hand; use `ALTER DEFAULT PRIVILEGES` so recreated tables keep
  their grants.
- Run a daily grant audit comparing expected vs actual privileges.
