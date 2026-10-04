.PHONY: setup run build-frontend test test-e2e coverage migrate seed seed-reference seed-demo demo benchmark-dashboard backup restore audit openapi postman-contract

setup:
	uv sync --all-groups
	powershell -NoProfile -Command "if (!(Test-Path .env)) { Copy-Item .env.example .env }"
	powershell -NoProfile -Command "if (Test-Path .git) { uv run pre-commit install } else { Write-Output 'Skipping pre-commit install: this folder is not a Git checkout.' }"
	npm --prefix frontend ci
	npm --prefix frontend run build

run: migrate build-frontend
	uv run uvicorn app.main:app --host 127.0.0.1 --port 8000

build-frontend:
	npm --prefix frontend run build

test:
	uv run pytest
	npm --prefix frontend test

test-e2e:
	npm --prefix frontend run test:e2e

coverage:
	uv run pytest --cov=app.security --cov=app.api.v1.routes.auth --cov-report=term-missing --cov-fail-under=80

migrate:
	uv run alembic upgrade head

seed-reference:
	uv run backend-seed

seed: migrate
	uv run backend-seed
	uv run backend-seed-demo

seed-demo: seed

demo:
	uv run python scripts/demo.py

benchmark-dashboard:
	uv run python scripts/benchmark_dashboard.py

backup:
	uv run python scripts/db_backup.py backup var/backend-backup.dump

restore:
	uv run python scripts/db_backup.py restore var/backend-backup.dump --yes

audit:
	uv run pip-audit --skip-editable

openapi:
	uv run python scripts/export_openapi.py

postman-contract: openapi
	uv run python scripts/openapi_to_postman.py
