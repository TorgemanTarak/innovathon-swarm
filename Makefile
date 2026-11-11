.PHONY: help setup demo dev test test-dashboard test-browser lint typecheck format check
help:
	@echo 'setup       Install locked runtime and development dependencies'
	@echo 'demo        Launch three isolated endpoints plus an incompatible device'
	@echo 'dev         Start Uvicorn coordinator with source reload'
	@echo 'check       Ruff lint, formatting, mypy, and tests'
	@echo 'test-dashboard  Dashboard regression tests (requires Node 22+)'
	@echo 'test-browser    Real Chromium dashboard tests (optional browser extra)'
	@echo 'format      Apply Ruff fixes and formatting'

setup:
	uv sync --frozen --extra dev

demo:
	uv run swarm demo --include-incompatible

dev:
	SWARM_DEMO_MODE=1 uv run uvicorn swarm.server:app --reload --reload-dir src --host 127.0.0.1 --port 8765

test:
	uv run pytest -q

test-dashboard:
	node --test tests/dashboard.test.cjs

test-browser:
	uv run --frozen --extra dev --extra browser pytest -q browser_tests

lint:
	uv run ruff check .
	uv run ruff format --check .

typecheck:
	uv run mypy

format:
	uv run ruff check --fix .
	uv run ruff format .

check: lint typecheck test
