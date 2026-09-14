set positional-arguments

BACKEND := "recipe-creator-backend"
FRONTEND := "recipe-creator-frontend"
UV := env_var_or_default("UV", "uv")
BUN := env_var_or_default("BUN", "bun")
SURREAL := env_var_or_default("SURREAL", "surreal")
UVICORN_WORKERS := env_var_or_default("UVICORN_WORKERS", "1")
CLI := UV + " run --project " + BACKEND + " python -m recipe_creator.cli"
API := UV + " run --project " + BACKEND + " python -m recipe_creator.server"

# Show the available commands and operational safety notes.
help:
    @just --list

# Install locked backend and frontend dependencies.
install:
    {{ UV }} sync --project {{ BACKEND }} --locked
    cd {{ FRONTEND }} && {{ BUN }} install --frozen-lockfile

# Start the local SurrealKV datastore.
db:
    {{ UV }} run --project {{ BACKEND }} --env-file .env {{ SURREAL }} start

# Start the API and frontend development servers together.
dev:
    #!/bin/sh
    WEB_CONCURRENCY={{ UVICORN_WORKERS }} {{ API }} --reload --reload-dir {{ BACKEND }}/src --host 127.0.0.1 --port 2332 --no-proxy-headers &
    api=$!
    {{ BUN }} run --cwd {{ FRONTEND }} dev &
    web=$!
    trap 'kill $api $web 2>/dev/null || true' INT TERM EXIT
    wait

# Start only the API development server.
dev-api:
    WEB_CONCURRENCY={{ UVICORN_WORKERS }} {{ API }} --reload --reload-dir {{ BACKEND }}/src --host 127.0.0.1 --port 2332 --no-proxy-headers

# Start only the frontend development server.
dev-web:
    {{ BUN }} run --cwd {{ FRONTEND }} dev

# Build backend and frontend production artifacts.
build:
    {{ UV }} build --project {{ BACKEND }}
    {{ BUN }} run --cwd {{ FRONTEND }} build

# Run backend syntax and frontend type checks.
check:
    {{ UV }} run --project {{ BACKEND }} python -m compileall -q {{ BACKEND }}/src
    {{ BUN }} run --cwd {{ FRONTEND }} check

# Run backend and frontend tests.
test: test-backend test-frontend

# Run backend tests.
test-backend:
    {{ UV }} run --project {{ BACKEND }} pytest {{ BACKEND }}/tests

# Run frontend tests.
test-frontend:
    {{ BUN }} run --cwd {{ FRONTEND }} test

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

# List recent active profiles for administrator permission changes.
admin-users *args:
    {{ CLI }} admin-users {{args}}

# Grant administrator permission to a user profile.
admin-grant *args:
    {{ CLI }} admin-grant {{args}}

# Revoke administrator permission from a user profile.
admin-revoke *args:
    {{ CLI }} admin-revoke {{args}}

# Export the current API OpenAPI schema.
export-openapi *args:
    {{ CLI }} export-openapi {{args}}
