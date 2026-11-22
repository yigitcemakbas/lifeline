PY ?= .venv/bin/python
PIP ?= $(PY) -m pip

.PHONY: install fmt lint typecheck test build check run cache evaluate

install:
	$(PIP) install -e ".[dev]"

fmt:
	$(PY) -m ruff format .
	$(PY) -m ruff check --fix .

lint:
	$(PY) -m ruff check .
	$(PY) -m ruff format --check .

typecheck:
	$(PY) -m mypy

test:
	$(PY) -m pytest

build:
	$(PY) -m build --wheel --outdir dist .

check: lint typecheck test

run:
	$(PY) -m streamlit run main.py

cache:
	$(PY) scripts/build_cache.py

evaluate:
	$(PY) -m lifeline.evaluation
