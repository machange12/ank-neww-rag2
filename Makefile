# Cross-platform dev shortcuts (macOS / Linux). Windows users can keep using
# the PowerShell commands in README.md — the Makefile is optional there.
#
# The venv lives at the REPO ROOT (config.py resolves .env relative to it).
# Usage:  make setup && make api

SHELL := /bin/sh
VENV := .venv
PYTHON ?= python3.11
ifeq ($(OS),Windows_NT)
PY := $(VENV)/Scripts/python
else
PY := $(VENV)/bin/python
endif

.PHONY: setup venv deps web-deps api worker web build test verify clean

setup: venv deps web-deps
	@echo "Setup complete. Activate the venv, copy .env.example to .env, then: make api"

venv:
	@test -d $(VENV) || $(PYTHON) -m venv $(VENV)

deps: venv
	$(PY) -m pip install --upgrade pip
	$(PY) -m pip install -r lawfirm-rag/requirements.txt

web-deps:
	cd frontend && npm ci

api:
	cd lawfirm-rag && ../$(PY) -m uvicorn app:app --host 0.0.0.0 --port 8000 --reload

worker:
	cd lawfirm-rag && ../$(PY) -m uvicorn ingest.worker:app --host 0.0.0.0 --port 8001 --reload

web:
	cd frontend && npm run dev

build:
	cd frontend && npm run build

test:
	cd lawfirm-rag && ../$(PY) -m pytest tests/ -q

verify:
	cd lawfirm-rag && ../$(PY) scripts/verify_schema.py --manifest-only

clean:
	rm -rf $(VENV) frontend/node_modules
