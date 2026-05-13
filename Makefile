.PHONY: check format ruff format-check pytest-cov openapi openapi-check ci migrate migrate-revision

PYTHON ?= .venv/bin/python

check:
	env PYTHONPATH= $(PYTHON) -m pytest tests/unit/ tests/architecture/ -q
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
	env PYTHONPATH= $(PYTHON) -m pytest tests/unit/ tests/architecture/ -q \
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
