.PHONY: setup test lint fixtures leakci stub-api ui sim-confluence sim-jira db-up db-down

setup:            ## create a venv, install dev dependencies, enable the secret-scan hook
	python3 -m venv .venv && .venv/bin/pip install -r requirements-dev.txt && .venv/bin/pre-commit install

test:             ## run contract tests and golden tests
	.venv/bin/python -m pytest

lint:
	.venv/bin/ruff check .

fixtures:         ## regenerate fixtures/company_a.json from fixtures/generate.py
	.venv/bin/python fixtures/generate.py

leakci:           ## Leak-CI on the fixture corpus (no database, no model); writes evals/scoreboard/leakci-latest.json
	.venv/bin/python -m evals.leakci --target fixture

stub-api:         ## run the stub Brain API on :8000 (auth: Authorization: Bearer dev:priya)
	.venv/bin/uvicorn brain.stub_api.app:app --reload --port 8000

ui:               ## run the Cortex UI on :5173, proxying /v1 and /sim to the stub API (run stub-api first)
	cd ui && npm install && npm run dev

brain-api:        ## run the real Brain API on :8000 (Postgres, simulators, Slack and Drive from .env)
	.venv/bin/uvicorn brain.api.main:app --port 8000

brain-api-fixture: ## run the real pipeline over the fixture corpus on :8000 (no database; /sim/advance for scripted events)
	BRAIN_RUNTIME=fixture .venv/bin/uvicorn brain.api.main:app --port 8000

sim-confluence:   ## run the Confluence simulator on :8101, seeded with Company A
	.venv/bin/uvicorn simulators.confluence.app:app --reload --port 8101

sim-jira:         ## run the Jira simulator on :8102, seeded with Company A
	.venv/bin/uvicorn simulators.jira.app:app --reload --port 8102

db-up:            ## local Postgres + pgvector with db/init.sql
	docker compose up -d

db-down:
	docker compose down
