set positional-arguments

BACKEND := "recipe-creator-backend"
FRONTEND := "recipe-creator-frontend"
UV := env_var_or_default("UV", "uv")
NPM := env_var_or_default("NPM", "npm")
SURREAL := env_var_or_default("SURREAL", "surreal")
UVICORN_WORKERS := env_var_or_default("UVICORN_WORKERS", "1")
CLI := UV + " run --project " + BACKEND + " python -m recipe_creator.cli"

# Show the available commands and operational safety notes.
help:
    @printf '%s\n' 'just install | db | dev | build | check | test' 'just migrate | cleanup | hash-password | export-openapi' 'just import snapshot.json --dry-run' 'just backup backup.tar.gz --confirm-quiesced' 'just restore backup.tar.gz --confirm-quiesced --confirm-trusted-archive' 'Configure private settings in .env; never put passwords in command arguments.' 'Database: just db starts a local SurrealKV datastore on 127.0.0.1:8000.' 'Backup/restore: stop API, workers and all other writers; leave DB running.' 'Restore: use a NEW database name and absent media path from .env. See CLI --help.'

# Install locked backend and frontend dependencies.
install:
    {{ UV }} sync --project {{ BACKEND }} --locked
    {{ NPM }} --prefix {{ FRONTEND }} ci

# Start the local SurrealKV datastore.
db:
    {{ UV }} run --project {{ BACKEND }} --env-file .env {{ SURREAL }} start

# Start the API and frontend development servers together.
dev:
    #!/bin/sh
    WEB_CONCURRENCY={{ UVICORN_WORKERS }} {{ UV }} run --project {{ BACKEND }} uvicorn recipe_creator.app:create_app --factory --reload --reload-dir {{ BACKEND }}/src --host 127.0.0.1 --port 2332 --no-proxy-headers &
    api=$!
    {{ NPM }} --prefix {{ FRONTEND }} run dev &
    web=$!
    trap 'kill $api $web 2>/dev/null || true' INT TERM EXIT
    wait

# Start only the API development server.
dev-api:
    WEB_CONCURRENCY={{ UVICORN_WORKERS }} {{ UV }} run --project {{ BACKEND }} uvicorn recipe_creator.app:create_app --factory --reload --reload-dir {{ BACKEND }}/src --host 127.0.0.1 --port 2332 --no-proxy-headers

# Start only the frontend development server.
dev-web:
    {{ NPM }} --prefix {{ FRONTEND }} run dev

# Build backend and frontend production artifacts.
build:
    {{ UV }} build --project {{ BACKEND }}
    {{ NPM }} --prefix {{ FRONTEND }} run build

# Run backend syntax and frontend type checks.
check:
    {{ UV }} run --project {{ BACKEND }} python -m compileall -q {{ BACKEND }}/src
    {{ NPM }} --prefix {{ FRONTEND }} run check

# Run backend and frontend tests.
test: test-backend test-frontend

# Run backend tests.
test-backend:
    {{ UV }} run --project {{ BACKEND }} pytest {{ BACKEND }}/tests

# Run frontend tests.
test-frontend:
    {{ NPM }} --prefix {{ FRONTEND }} test

# Apply pending database migrations.
migrate *args='':
    {{ CLI }} migrate "$@"

# Import a recipe snapshot.
import *args='':
    {{ CLI }} import "$@"

# Create a database backup.
backup *args='':
    {{ CLI }} backup "$@"

# Restore a database backup.
restore *args='':
    {{ CLI }} restore "$@"

# Remove expired or orphaned data.
cleanup *args='':
    {{ CLI }} cleanup "$@"

# Hash an administrator password.
hash-password *args='':
    {{ CLI }} hash-password "$@"

# Export the current API OpenAPI schema.
export-openapi *args='':
    {{ CLI }} export-openapi "$@"
