.PHONY: dev-install dev-run check check-integration test format ruff format-check pytest-cov \
        openapi openapi-check ci migrate migrate-revision

PYTHON ?= .venv/bin/python
# PYTHONPATH is cleared because a sourced ROS workspace adds pytest plugins that break the suite.
PYTEST = env PYTHONPATH= $(PYTHON) -m pytest -q

dev-install:
	$(PYTHON) -m pip install -e ../leitstand-robot-contract
	$(PYTHON) -m pip install -e ".[dev]"

# Deps in containers, backend native for fast iteration.
dev-run:
	@[ -d secrets ] || cp -r secrets.example secrets
	-docker compose stop backend 2>/dev/null
	docker compose up -d --wait postgres zenoh-router
	-docker compose up -d --build coverage-planner
	LEITSTAND_COVERAGE_PLANNER_URL=$${LEITSTAND_COVERAGE_PLANNER_URL:-http://localhost:8090} \
	    $(PYTHON) -m leitstand_backend

check: ruff format-check test

test:
	$(PYTEST)

# Creates, migrates and drops its own database on a development Postgres, never a production one.
check-integration:
	$(PYTEST) -m integration

format:
	$(PYTHON) -m ruff check --fix .
	$(PYTHON) -m ruff format .

ruff:
	$(PYTHON) -m ruff check .

format-check:
	$(PYTHON) -m ruff format --check .

pytest-cov:
	$(PYTEST) --cov=leitstand_backend --cov-report=term --cov-report=xml

openapi:
	$(PYTHON) scripts/dump_openapi.py

openapi-check:
	$(PYTHON) scripts/dump_openapi.py
	git diff --exit-code openapi.json

ci: ruff format-check pytest-cov openapi-check

migrate:
	$(PYTHON) -m alembic -c migrations/alembic.ini upgrade head

migrate-revision:
	$(PYTHON) -m alembic -c migrations/alembic.ini revision --autogenerate -m "$(name)"
