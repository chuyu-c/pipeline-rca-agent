# 0003 — Ship a CLI, not the API/worker service

Status: accepted (Phase 8 cut)

## Context
DESIGN planned a FastAPI service (alert webhook, incident read, `/approve`, feedback, `/metrics`) and a
worker draining a Postgres job queue (`SELECT … FOR UPDATE SKIP LOCKED`, leases, retries, dead-letter).
With a fixed time budget, the question was what a reviewer needs to evaluate the core claim: *does an
LLM agent with guardrails find root causes better than a deterministic baseline, safely?*

## Decision
Every capability is reachable from the command line instead of HTTP: `make demo` (inject → pipeline →
monitors → triage), `python -m rca.agent triage|show`, `python -m rca.faults`, `eval/run_eval.py`.
The data model keeps the service seams: alert ingest still enqueues a debounced, idempotent `triage`
row in `ops.jobs` per incident, and RCAs and traces are persisted on `ops.incidents`.

## Consequences
- **No execution path for remediation.** `proposed_actions` are shown (`rca show`) but nothing executes
  them; the approve-with-server-side-validation step of DESIGN §5.3 does not exist. This is safe by
  construction (the agent has no write tools either), but it is a gap, not a feature.
- **Jobs accumulate.** `ops.jobs` rows stay `queued` (reset cancels them); triage runs synchronously in
  the CLI. The queue semantics in DESIGN §9 are designed but untested.
- **No `/metrics`.** Per-incident cost/steps/latency are in `ops.incidents.rca` and the eval report.
- Reviewers can run everything with one command and no extra processes; the eval exercises the same
  code path a worker would call (`run_triage` + `persist_outcome`).
- Building the service later is additive: a worker loop around `service.triage_incident`, and thin
  FastAPI routes over `ops.incidents`.
