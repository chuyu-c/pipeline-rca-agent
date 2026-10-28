# AI Workflow


## 1. What the project does

An AI-assisted triage service for data-pipeline incidents. A synthetic e-commerce warehouse runs a small DAG, and a fault injector breaks it in nine ways (schema drift, bad deploy, null spike, etc.). This tool monitors group alerts into incidents by lineage path. An agent investigates with read-only tools (task logs, lineage, guarded SQL, deploy history, hybrid runbook retrieval) and returns a cited RCA with a *proposed* fix that will require human approval. 

## 2. Why this project

It exercises the core of AI automation for data-engineering ops in one system, including alert noise reduction, RAG over runbooks and postmortems, and a tool-using agent. 

It also builds on some projects I have done at my current position. I created a simpler on-call summary agent and data quality check agent that my team uses daily, but its setup and workflow are completely different. Here I wanted to build something that reflects a more structured engineering pipeline, both in the system and in how it was built.

- **A real pipeline to debug.** The `ecommerce_daily` DAG has 7 tasks across three layers (raw → stg → mart), partitioned by `logical_date`. Two ingest tasks feed `raw.orders`, `raw.order_items` and `raw.inventory_snapshots`. Two staging tasks clean them, and three marts (GMV by region, SKU sell-through, inventory health) sit downstream. 
- **Lineage from the SQL itself.** Table and column lineage is parsed from each task's SQL with sqlglot, so the agent and the alert correlation reason over the real dependency graph.
- **Engineering process.** A written design and build plan, phased delivery, guardrails enforced by tests, and an eval harness with a baseline.

## 3. AI tools and models

- **Claude (chat):** upfront design. It produced `docs/DESIGN.md` and `CLAUDE.md` before any code.
- **Claude Code:** for implementation.
- **In the product:** the agent calls the Anthropic API through an `LLMClient` protocol (model set by `RCA_MODEL`). The latest live eval ran on `claude-opus-5-5`.


## 4. How AI fit into the workflow

- **CLAUDE.md as the contract.** It fixes the stack, the `make` interface and the rules the AI must follow:
  LLM calls only via `LLMClient`, tool output treated as untrusted, no write tools, and all LLM-written SQL
  through `sql_guard` *and* the `rca_readonly` role. It lists four guardrail invariants that tests must enforce.
- **Plan mode per phase.** I input project milestones for each phase, edited the plan, then let it implement. I manually verify the progress in every phase.
- **Format-on-edit hook** (`.claude/settings.json`) runs ruff after every edit, so style never came up in review.
- **Offline tests.** `make test` needs no Docker, network or API key. Unit tests use `ScriptedLLM`, `ReplayLLM`
  and `HashEmbedder`. Live runs are recorded as transcripts in `tests/fixtures/llm/`, so agent behaviour is
  regression-tested offline. The SQL guard alone has 24 adversarial tests.
- **Measured results.** `make eval` scores 9 scenarios; `make eval-baseline` runs the fallback only.

| metric | baseline (no LLM) | agent (live) |
|---|---|---|
| category accuracy | 89% | 100% |
| root entity accuracy | 22% | 89% |
| citation recall@5 | 0.00 | 1.00 |
| guardrail violations | 0 | 0 |

- **Review.** I read code against each phase's checklist and read full live traces to confirm every RCA claim
  had evidence.

## 5. Examples

**Design change driven by the data (ADR 0001).** The original rule attached an alert to an incident when the
datasets shared a lineage ancestor. Nearly every table descends from `raw.products`, so unrelated breaks would
merge. We switched to "same lineage path", then amended it when `duplicate_load` split into two incidents
(two tables written by one task). Grouping purity is now 1.00 on all nine scenarios.

**Citations that looked grounded but weren't.** In live runs the model cited tool-call ids it never made
(e.g. `get_task_runs:0`). My first check only confirmed the id existed. Validation now also requires the quoted
excerpt to appear in that call's output, and tool results carry their `tool_call_id` so the model can copy it.

**Productivity win: scenarios first, then replay.** I wrote the nine fault scenarios, each with ground truth
(category, root entity, expected runbooks), before the agent existed. Every later phase then had a pass/fail
target instead of "looks reasonable". Once the agent worked, I recorded one live run per scenario, including
every tool output the model saw, as replay fixtures. A full live eval takes about 6 minutes and costs about
$1.50. A replay re-scores the same runs with no API calls, so I could change the scoring, report format
and validation logic and re-run all nine scenarios for free. Because replay serves the recorded tool outputs
instead of re-running the tools, it reproduces the original run exactly, and a call that wasn't recorded fails
loudly as drift instead of silently passing.

## 6. Where AI fell short

- **Citation discipline** needed code enforcement, not just prompting; I found each issue by reading live traces.
- **`resource` scenario still misses the root entity** (`raw.order_items` vs `stg_order_items`) even though its
  summary correctly says the timeout was too small. It is a reasoning miss, not retrieval (recall@5 is 1.00).
- **I loosened the root-entity metric after seeing results.** It now accepts a column of the expected table,
  for agent and baseline alike. Read the 89% with that in mind.
- **Hybrid retrieval did not beat keyword search** on `bad_deploy` (recall 0.94 vs 1.00), contrary to the plan.
- **Unfinished:** the worker queue, FastAPI service, README and CI (phases 8 and 10).
- **Small sample:** one live run per scenario on synthetic data shows the pipeline works, not real-world accuracy.
