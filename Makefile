.PHONY: dev-install dev-run check format ruff format-check pytest-cov openapi openapi-check ci migrate migrate-revision

PYTHON ?= .venv/bin/python

dev-install:
	$(PYTHON) -m pip install -e ../leitstand-robot-contract
	$(PYTHON) -m pip install -e ".[dev]"

# Deps in containers, backend native for fast iteration
dev-run:
	docker compose up -d --wait postgres zenoh-router
	$(PYTHON) -m leitstand_backend

check:
	env PYTHONPATH= $(PYTHON) -m pytest -q
	$(PYTHON) -m ruff check .
	$(PYTHON) -m ruff format --check .

format:
	$(PYTHON) -m ruff format .
	$(PYTHON) -m ruff check --fix .

ruff:
	$(PYTHON) -m ruff check .

format-check:
	$(PYTHON) -m ruff format --check .

pytest-cov:
	env PYTHONPATH= $(PYTHON) -m pytest -q \
	    --cov=leitstand_backend --cov-report=term --cov-report=xml

openapi:
	$(PYTHON) scripts/dump_openapi.py

openapi-check:
	$(PYTHON) scripts/dump_openapi.py
	git diff --exit-code openapi.json

ci: ruff format-check pytest-cov openapi-check

migrate:
	env LEITSTAND_DATABASE_URL=$${LEITSTAND_DATABASE_URL:-postgresql+asyncpg://leitstand:leitstand@localhost:5432/leitstand} \
	  $(PYTHON) -m alembic -c migrations/alembic.ini upgrade head

migrate-revision:
	env LEITSTAND_DATABASE_URL=$${LEITSTAND_DATABASE_URL:-postgresql+asyncpg://leitstand:leitstand@localhost:5432/leitstand} \
	  $(PYTHON) -m alembic -c migrations/alembic.ini revision --autogenerate -m "$(name)"
