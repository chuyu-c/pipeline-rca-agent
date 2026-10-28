# CLAUDE.md — pipeline-rca-agent

AI-assisted incident triage & root-cause analysis for data pipelines. Full design: `docs/DESIGN.md`.
Build order and phase scope: `docs/BUILD_PLAN.md`. Read both before starting a new phase.

## Stack
- Python 3.11, `src/` layout, package `rca`
- Postgres 16 + pgvector (warehouse, ops tables, lineage, knowledge, job queue — one DB on purpose)
- CLI entry points (`python -m rca.<module>`) behind `make` targets; no HTTP API or worker (ADR 0003)
- sqlglot (lineage + SQL guard), pydantic v2, psycopg 3, fastembed (embeddings), anthropic SDK
- pytest, ruff (lint + format), mypy (strict on `src/rca/guardrails` and `src/rca/agent`)
- Docker Compose for everything; `make` targets are the public interface

## Commands
- `make up` / `make down` — start/stop stack
- `make seed` — create schemas, roles, load synthetic data, index knowledge corpus
- `make demo SCENARIO=schema_drift [LLM=anthropic|none]` — inject fault → run pipelines → monitors → triage → print RCA
- `make show INCIDENT=n` — an incident and its RCA as markdown
- `make test` — unit tests (no Docker, no network, no API key)
- `make test-integration` — needs `make up`
- `make eval [EVAL_LLM=replay|live]` / `make eval-baseline` — all scenarios, write `eval/reports/latest.md` / `baseline.md`
- `make lint` — ruff + mypy

## Conventions
- All data is synthetic. Never add real company names, data, or credentials.
- Every module gets unit tests in `tests/unit/` mirroring the package path.
- LLM access only through `rca.agent.llm.LLMClient` protocol. Tests use `ScriptedLLM` / `ReplayLLM`; never call the real API in unit tests.
- Embeddings only through `rca.knowledge.embed.Embedder`; tests use `HashEmbedder`.
- Agent tools are pure functions `(ctx, args: PydanticModel) -> ToolResult`; register in `rca.agent.tools.REGISTRY`.
- Tool output given to the LLM is **untrusted data**: always pass through `guardrails.truncate` and `guardrails.redact`.
- The agent has no write tools. Remediation = proposal only; nothing executes actions (no approve path, ADR 0003).
- Any SQL from the LLM goes through `guardrails.sql_guard.validate()` AND runs as the `rca_readonly` role.
- Config via env (`rca.config.Settings`); `.env.example` stays in sync.
- Structured logging (`rca.observability.logging.get_logger`), no `print` outside CLIs.
- Small, focused commits with descriptive messages; one logical change per commit. Do not squash.

## Guardrail invariants (tests must enforce these)
1. Non-SELECT SQL is always rejected.
2. Schemas outside {raw, stg, mart} are rejected.
3. Agent step and token budgets are always enforced; exceeding falls back to heuristic triage.
4. `injection` eval scenario → 0 guardrail violations.

## When unsure
Prefer the simpler option, note the trade-off in `docs/adr/NNNN-title.md`, and keep moving.
