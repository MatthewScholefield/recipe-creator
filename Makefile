BACKEND := recipe-creator-backend
FRONTEND := recipe-creator-frontend
UV ?= uv
NPM ?= npm
CLI := $(UV) run --project $(BACKEND) python -m recipe_creator.cli
ARGS ?=

.PHONY: help install dev dev-api dev-web build check test test-backend test-frontend migrate import backup restore cleanup hash-password export-openapi
help:
	@printf '%s\n' 'make install | dev | build | check | test' 'make migrate | cleanup | hash-password | export-openapi' 'make import ARGS="snapshot.json --dry-run"' 'make backup ARGS="backup.tar.gz --confirm-quiesced"' 'make restore ARGS="backup.tar.gz --confirm-quiesced --confirm-trusted-archive"' 'Set private RECIPE_* environment configuration; never put passwords in command arguments.' 'Backup/restore: stop API, workers and all other writers; leave DB running.' 'Restore: use a NEW RECIPE_DB_DATABASE and absent RECIPE_MEDIA_ROOT. See CLI --help.'
install:
	$(UV) sync --project $(BACKEND) --locked
	$(NPM) --prefix $(FRONTEND) ci

dev:
	@$(UV) run --project $(BACKEND) uvicorn recipe_creator.app:create_app --factory --reload --reload-dir $(BACKEND)/src --host 127.0.0.1 --port 8080 --no-proxy-headers & api=$$!; \
	$(NPM) --prefix $(FRONTEND) run dev & web=$$!; \
	trap 'kill $$api $$web 2>/dev/null || true' INT TERM EXIT; wait

dev-api:
	$(UV) run --project $(BACKEND) uvicorn recipe_creator.app:create_app --factory --reload --reload-dir $(BACKEND)/src --host 127.0.0.1 --port 8080 --no-proxy-headers

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
	$(CLI) $@ $(ARGS)
