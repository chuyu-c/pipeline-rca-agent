SCENARIO ?= schema_drift
DATE ?= $(shell date +%F)
EXEC := docker compose exec app

.PHONY: install up down migrate seed lineage run demo test test-integration eval lint fmt

install:  ## Create .venv with dev extras
	uv sync --extra dev

up:  ## Start the stack and wait for healthchecks
	docker compose up -d --build --wait

down:  ## Stop the stack
	docker compose down

migrate:  ## Apply pending SQL migrations (schemas, roles, tables)
	$(EXEC) python -m rca.db migrate

seed: migrate  ## Create schemas/roles, load synthetic data, build lineage (knowledge: Phase 5)
	$(EXEC) python -m rca.simulator seed --days 14 --end-date $(DATE)
	$(EXEC) python -m rca.lineage build

lineage:  ## Rebuild lineage.edges from DAG SQL
	$(EXEC) python -m rca.lineage build

run:  ## Run the daily DAG for DATE (default: today)
	$(EXEC) python -m rca.simulator run --date $(DATE)

demo:  ## Inject SCENARIO, run pipelines, triage, print RCA
	@echo "make demo SCENARIO=$(SCENARIO): not implemented yet (Phase 4/7)" && exit 1

test:  ## Unit tests — no Docker, no network, no API key
	uv run --extra dev pytest -m "not integration and not system"

test-integration:  ## Needs `make up`
	uv run --extra dev pytest -m integration

eval:  ## Run all scenarios, write eval/reports/latest.md
	@echo "make eval: not implemented yet (Phase 9)" && exit 1

lint:  ## ruff + mypy
	uv run --extra dev ruff check src tests
	uv run --extra dev ruff format --check src tests
	uv run --extra dev mypy

fmt:  ## Auto-format and fix lint
	uv run --extra dev ruff format src tests
	uv run --extra dev ruff check --fix src tests
