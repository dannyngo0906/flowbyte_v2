.PHONY: dev db-up db-down install lint format typecheck test init clean dbt-deps dbt-build dbt-test dbt-freshness dbt-seed

PYTHON ?= python3.11
VENV ?= .venv
COMPOSE ?= $(shell command -v docker-compose 2>/dev/null || echo "docker compose")

dev: db-up install
	$(VENV)/bin/pre-commit install

db-up:
	$(COMPOSE) up -d
	@echo "Waiting for Postgres healthy..."
	@for i in 1 2 3 4 5 6 7 8 9 10; do \
		$(COMPOSE) exec -T postgres pg_isready -U elt_user -d haravan >/dev/null 2>&1 && exit 0; \
		sleep 2; \
	done; echo "Postgres did not become healthy" && exit 1

db-down:
	$(COMPOSE) down

install:
	$(PYTHON) -m venv $(VENV)
	$(VENV)/bin/pip install --upgrade pip
	$(VENV)/bin/pip install -e ".[dev]"

lint:
	$(VENV)/bin/ruff check src tests
	$(VENV)/bin/ruff format --check src tests

format:
	$(VENV)/bin/ruff format src tests
	$(VENV)/bin/ruff check --fix src tests

typecheck:
	$(VENV)/bin/mypy src

test:
	$(VENV)/bin/pytest

init:
	$(VENV)/bin/haravan-elt init

dbt-deps:
	cd dbt && DBT_PROFILES_DIR=. ../$(VENV)/bin/dbt deps

dbt-build:
	cd dbt && DBT_PROFILES_DIR=. ../$(VENV)/bin/dbt build --select staging

dbt-test:
	cd dbt && DBT_PROFILES_DIR=. ../$(VENV)/bin/dbt test --select staging

dbt-freshness:
	cd dbt && DBT_PROFILES_DIR=. ../$(VENV)/bin/dbt source freshness

dbt-seed:
	psql "$$DATABASE_URL" -f scripts/seed-raw-fixtures.sql

clean:
	rm -rf $(VENV) .pytest_cache .ruff_cache .mypy_cache .coverage coverage.xml dbt/target dbt/logs
	find . -type d -name __pycache__ -prune -exec rm -rf {} +
