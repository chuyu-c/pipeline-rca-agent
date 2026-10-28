# 0001 — Correlate alerts by lineage path, not shared ancestry

Status: accepted (Phase 3)

## Context
DESIGN §5.1 originally attached a new alert to an open incident when their datasets "share a lineage
ancestor". In the synthetic warehouse almost every table descends from the static `raw.products`
dimension (it feeds `stg.order_items`, `mart.sku_sell_through` and `mart.inventory_health`), and all
marts descend from a handful of raw tables. Under that rule, two unrelated breaks on the same day — say
a null spike in `raw.orders` and a missing `raw.inventory_snapshots` partition — would merge into one
incident, hurting grouping purity (a DESIGN §2 metric) and handing the agent a muddled investigation.

## Decision
Attach only when the datasets lie on the **same lineage path**: equal, or one is an ancestor of the
other. An upstream break and its downstream symptoms (failed task + freshness alerts on the marts it
feeds) form one incident; sibling branches stay separate. Datasets unknown to lineage correlate only by
equality.

Incidents are never merged. To keep a batch from opening one incident per sibling symptom before their
shared cause arrives, each monitor batch is ingested upstream-first (topological order).

## Consequences
- One upstream break → one incident (integration-tested with the schema-drift case).
- Two symptoms of one hidden cause that is *not itself alerted* (e.g. `mart.x` and `mart.y` both broken by
  an unalerted `stg.a`) open two incidents. Acceptable: a real break almost always alerts at its own
  node (task failure or freshness), and the agent investigates lineage from either incident.
- If alerts for one cause arrive across separate batches with siblings first, they can still split.
  An incident-merge step would fix that; deferred until eval shows it matters.

## Amendment (Phase 4): tables written by the same task correlate
`duplicate_load` breaks `raw.orders` and `raw.order_items` together — one `ingest_orders` load writes
both — but neither is an ancestor of the other, so the path rule split one break into two incidents.
Datasets that share a producing task now also count as the same path. This only adds edges between
outputs of one task, so it doesn't reintroduce the over-merging the original rule avoided.
