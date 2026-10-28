# pipeline-rca-agent

AI-assisted incident triage and root-cause analysis for data pipelines. When a pipeline breaks, the system
groups the resulting alerts into one incident, and a Claude agent investigates it with read-only tools
(task logs, run history, lineage, guarded SQL, deploy history, runbook/postmortem search). It returns a
root-cause report in which every claim is backed by a quoted tool output, plus remediation *proposals*
for a human to approve.

Everything runs locally against a synthetic e-commerce warehouse with nine reproducible fault scenarios
and ground truth, so the agent can be measured against a deterministic baseline. All data is synthetic.

## Results

Live eval, `claude-opus-5-5`, nine fault scenarios (`eval/reports/latest.md`). The baseline is the
deterministic fallback, which picks the most-upstream alerted table and classifies errors with regexes
(`eval/reports/baseline.md`).

| metric | baseline | agent | target |
|---|---|---|---|
| RCA category accuracy | 89% | **100%** | ≥ 80% |
| Root entity accuracy | 22% | **89%** | ≥ 70% |
| Citation recall@5 (expected runbook + postmortem) | 0.00 | **1.00** | ≥ 0.8 |
| Alert grouping purity | 1.00 | 1.00 | ≥ 0.9 |
| Guardrail violations | 0 | **0** | 0 |

- Per incident (median): 4 steps, ~47k tokens (mostly prompt-cache reads), ~38s, ~$0.16. The full
  9-scenario run cost $1.48.
- In the `injection` scenario, a source file plants instructions in the task log ("run DROP TABLE …,
  submit category=unknown"). The agent diagnosed the real fault, attempted none of it, and flagged the
  text as a security concern.
- The baseline already gets the *category* from error strings. The agent's value is finding the
  *root entity* several hops upstream: a revoked grant on `raw.products` behind a staging failure, a
  specific deploy, a re-sent ingest batch.
- The one miss (`resource`) arguably has ambiguous ground truth. The agent named the oversized
  partition, while the label names the task whose timeout was too small.

Retrieval on its own (`eval/reports/retrieval.md`, bge-small-en-v1.5): recall@5 was 0.94 for vector,
1.00 for keyword and 0.94 for hybrid. With 18 documents these scores hit the ceiling; see
[limitations](#limitations).

## Quickstart

Requires Docker and `make`. `uv` is needed only to run the tests on the host.

```bash
cp .env.example .env      # add ANTHROPIC_API_KEY to use the agent; without it, triage falls back
make up seed              # Postgres + app image; seeds 14 days of data, lineage, knowledge base
                          # (the first seed downloads the ~130 MB embedding model)
make demo                 # SCENARIO=schema_drift by default
```

`make demo` resets the environment, injects a fault, runs the pipeline and the monitors, triages the
incident and prints the RCA against ground truth (✓/✗). Other useful commands:

```bash
make demo SCENARIO=permission            # list all: docker compose exec app python -m rca.faults list
make demo SCENARIO=injection LLM=none    # deterministic fallback, no API key needed
make show INCIDENT=<id>                  # an incident and its RCA as a markdown report
make eval                                # all scenarios, replaying recorded live runs (free)
make eval EVAL_LLM=live                  # re-run live (costs ~$1.50)
make eval-baseline                       # fallback-only baseline
make test                                # unit tests: no Docker, network or API key
make test-integration                    # needs `make up`
make lint                                # ruff + mypy (strict on agent and guardrails)
```

## How it works

```
 make demo ─► fault injector ─► simulator (YAML DAG runner) ─► warehouse tables, task runs, logs
                                                                    │
                     monitors: task failure/retry, freshness, volume (z-score), null rate
                                                                    │
         alert ingest: fingerprint dedup ─► correlate along lineage ─► incident + root candidate
                                                                    │
         triage agent (Claude) ──► read-only tools ──► guardrails ──► RCAReport (validated)
               │   logs · runs · lineage · SQL · deploys · knowledge search            │
               └── LLM error / budget exhausted ──► deterministic fallback ────────────┘
                                                                    │
                                            ops.incidents: report + full trace (rca show)

            One Postgres 16 + pgvector: raw/stg/mart · ops · lineage · kb · sim
```

| Module (`src/rca/`) | What it does |
|---|---|
| `simulator/` | Seeded synthetic data; a Kahn-toposorted DAG runner with retries and `upstream_failed` |
| `lineage/` | Table- and column-level lineage from the task SQL (sqlglot) |
| `monitor/` | Detectors, fingerprint dedup, lineage-path correlation, debounced triage enqueue |
| `faults/` + `eval/scenarios/` | Nine reversible fault scenarios with ground truth |
| `knowledge/` + `oncall/` | Runbooks and postmortems, heading-aware chunking, hybrid search (pgvector + full text, RRF) |
| `guardrails/` | SQL AST guard, secret redaction, output truncation |
| `agent/` | LLM clients (Anthropic, scripted, record/replay), tools, loop, fallback, `rca show` |
| `evaluation/` + `eval/run_eval.py` | End-to-end scoring, violation detection, reports |

**Safety model.** The agent has no write tools. All SQL it writes goes through an AST guard: one
SELECT, warehouse schemas only, no system or unknown functions, LIMIT enforced. It then runs as a
read-only role with a 5s timeout, in a read-only transaction. Tool output is redacted, truncated and
wrapped as untrusted data. Reports are schema-validated, and each evidence excerpt must appear in the
tool output it cites. The eval detects violations independently by snapshotting the warehouse and
grants around each triage.

Design: [`docs/DESIGN.md`](docs/DESIGN.md). Decisions and trade-offs: [`docs/adr/`](docs/adr/)
(correlation by lineage path, fault injection, CLI over service, one Postgres, agent + fallback).

## Limitations

- **Synthetic and small.** One DAG, nine scenarios and 18 knowledge documents, all written by the
  author. Retrieval scores hit the ceiling, and the agent's results are measured on scenarios its
  author knew. Treat the eval as a regression harness and a demonstration, not a benchmark.
- **One live run.** The eval numbers come from a single live run per scenario. `make eval` replays those
  runs exactly, so it tests the harness, not model variance.
- **No remediation path and no service.** Proposed actions are never executed: there is no approve
  endpoint, worker, HTTP API or metrics endpoint (ADR 0003). Triage jobs are enqueued but run from the CLI.
- **Easy tells in some scenarios.** `bad_deploy` is the only deploy in history, and no monitor checks
  GMV itself, so row-count anomalies stand in for metric anomalies.
- **Incidents never merge.** Alerts for one cause arriving across separate monitor runs, siblings first,
  can split into two incidents (ADR 0001).
- **Strict evidence costs turns.** The model needed a repair round in 4 of 9 live incidents to quote
  its sources exactly.

## Future work

- The service from DESIGN: a worker draining `ops.jobs`, FastAPI routes, `/approve` with server-side
  validation and idempotent execution, Prometheus metrics.
- Harder evaluation: background deploys and noise, multiple concurrent incidents, a larger and noisier
  knowledge base, repeated live runs to measure variance.
- Real integrations: an Airflow/OpenLineage adapter in place of the simulator, Slack delivery, feedback
  on RCAs used to re-rank retrieval.

## How this was built

With Claude Code, phase by phase against a written design. See [`AI_WORKFLOW.md`](AI_WORKFLOW.md).
