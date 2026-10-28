# 0002 — Inject faults through a hidden `sim.faults` table

Status: accepted (Phase 4)

## Context
DESIGN §10's scenarios need the environment to misbehave in ways a one-off SQL mutation before the
run can't express: a source file that hasn't landed, a source that sends NULL regions, an upload that
is re-sent, a transient timeout that heals on retry, a deploy that changes what SQL runs. Mutating
data *after* a run would leave task logs that contradict the data (ingest says "loaded 301 orders"
while 602 exist), which would make the eval unfair to the agent in either direction.

## Decision
Scenarios are YAML (`eval/scenarios/*.yaml`) with `setup` / `teardown` steps (admin-role SQL or a
python hook) and ground truth. Behavioural faults are written to `sim.faults` (`source:<name>` or
`task:<task_id>` → params). The simulator's ingest tasks and `PgExecutor` read it and behave — and
log — the way the real failure would: "No file found at landing/inventory/…", "Upload acknowledgement
… timed out; re-sending batch", "Executing sql/stg/order_items.sql (deploy d-1042, commit 9f3c2ab)".
Structural faults (column rename, revoked grant) are plain SQL steps with an idempotent teardown.

`sim` is granted to `rca_app` only. The agent's `rca_readonly` role and its tools never read it, so
scenarios can't leak their answers.

`reset` runs every teardown, clears `sim.faults`, resolves open incidents, and deletes the logical
date's partitions and scheduler history so each demo/eval run is that day's first run (otherwise a
failed rerun leaves the previous good partitions in place and freshness never fires).

## Consequences
- The simulator has a small amount of fault-aware code (`rca/simulator/conditions.py`, ingest,
  executor). It is inert when `sim.faults` is empty, which the integration test checks after every
  scenario's reset.
- Schema drift is modelled as the source renaming a column and the loader auto-evolving the raw table,
  so ingest succeeds and staging fails — the realistic shape of that incident.
- The `bad_deploy` deploy is the only deploy in history, which makes it easy to spot. Seeding benign
  background deploys would make that scenario harder; left for when the eval shows it is too easy.
