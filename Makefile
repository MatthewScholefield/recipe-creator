BACKEND := recipe-creator-backend
FRONTEND := recipe-creator-frontend
UV ?= uv
NPM ?= npm
SURREAL ?= surreal
UVICORN_WORKERS ?= 1
CLI := $(UV) run --project $(BACKEND) python -m recipe_creator.cli
CLI_COMMANDS := migrate import backup restore cleanup hash-password export-openapi
CLI_ARGS := $(if $(filter $(CLI_COMMANDS),$(MAKECMDGOALS)),$(filter-out $(CLI_COMMANDS),$(MAKECMDGOALS)))
ARGS ?=

# Positional goals after a CLI command are forwarded as CLI arguments. The
# explicit no-op rule prevents make from trying to build those argument names.
$(CLI_ARGS):
	@:

.PHONY: help install db dev dev-api dev-web build check test test-backend test-frontend migrate import backup restore cleanup hash-password export-openapi
help:
	@printf '%s\n' 'make install | db | dev | build | check | test' 'make migrate | cleanup | hash-password | export-openapi' 'make -- import snapshot.json --dry-run' 'make -- backup backup.tar.gz --confirm-quiesced' 'make -- restore backup.tar.gz --confirm-quiesced --confirm-trusted-archive' 'Configure private settings in .env; never put passwords in command arguments.' 'Admin: run make hash-password, then set RECIPE_ADMIN_PASSWORD_HASH in .env.' 'Database: make db starts a local SurrealKV datastore on 127.0.0.1:8000.' 'Backup/restore: stop API, workers and all other writers; leave DB running.' 'Restore: use a NEW database name and absent media path from .env. See CLI --help.'
install:
	$(UV) sync --project $(BACKEND) --locked
	$(NPM) --prefix $(FRONTEND) ci

db:
	$(UV) run --project $(BACKEND) --env-file .env $(SURREAL) start

dev:
	@WEB_CONCURRENCY=$(UVICORN_WORKERS) $(UV) run --project $(BACKEND) uvicorn recipe_creator.app:create_app --factory --reload --reload-dir $(BACKEND)/src --host 127.0.0.1 --port 2332 --no-proxy-headers & api=$$!; \
	$(NPM) --prefix $(FRONTEND) run dev & web=$$!; \
	trap 'kill $$api $$web 2>/dev/null || true' INT TERM EXIT; wait

dev-api:
	WEB_CONCURRENCY=$(UVICORN_WORKERS) $(UV) run --project $(BACKEND) uvicorn recipe_creator.app:create_app --factory --reload --reload-dir $(BACKEND)/src --host 127.0.0.1 --port 2332 --no-proxy-headers

dev-web:
	$(NPM) --prefix $(FRONTEND) run dev

build:
	$(UV) build --project $(BACKEND)
	$(NPM) --prefix $(FRONTEND) run build

check:
	$(UV) run --project $(BACKEND) python -m compileall -q $(BACKEND)/src
	$(NPM) --prefix $(FRONTEND) run check

test: test-backend test-frontend

test-backend:
	$(UV) run --project $(BACKEND) pytest $(BACKEND)/tests

test-frontend:
	$(NPM) --prefix $(FRONTEND) test

migrate import backup restore cleanup hash-password export-openapi:
	$(CLI) $@ $(CLI_ARGS) $(ARGS)
