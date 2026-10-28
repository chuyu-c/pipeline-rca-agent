SCENARIO ?= schema_drift
DATE ?= $(shell date +%F)
EXEC := docker compose exec app

.PHONY: install up down migrate seed lineage run monitor reset demo test test-integration eval lint fmt

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

monitor:  ## Run detectors for DATE and ingest alerts into incidents
	$(EXEC) python -m rca.monitor run --date $(DATE)

reset:  ## Undo all fault scenarios and resolve open incidents
	$(EXEC) python -m rca.faults reset --date $(DATE)

demo:  ## Reset, inject SCENARIO, run the pipeline and monitors, print the incident (triage: Phase 7)
	$(EXEC) python -m rca.faults demo $(SCENARIO) --date $(DATE)

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
