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
    @just --list

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
migrate *args:
    {{ CLI }} migrate {{args}}

# Import a recipe snapshot.
import *args:
    {{ CLI }} import {{args}}

# Create a database backup.
backup *args:
    {{ CLI }} backup {{args}}

# Restore a database backup.
restore *args:
    {{ CLI }} restore {{args}}

# Remove expired or orphaned data.
cleanup *args:
    {{ CLI }} cleanup {{args}}

# Hash an administrator password.
hash-password *args:
    {{ CLI }} hash-password {{args}}

# Export the current API OpenAPI schema.
export-openapi *args:
    {{ CLI }} export-openapi {{args}}
