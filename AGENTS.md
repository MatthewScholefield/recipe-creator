# Project guide

- `recipe-creator-frontend/`: Svelte 5 + TypeScript + Vite; `src/` UI, `e2e/` Playwright.
- `recipe-creator-backend/`: FastAPI; `src/recipe_creator/` routes/services/models, `migrations/`, `tests/`.
- Root `justfile`: development, checks, builds, and maintenance. Configuration uses `RECIPE_*` environment variables.

## Minimal validation

Run `just check` for a quick syntax/type check. Run tests for the changed area:

- Backend: `uv run --project recipe-creator-backend pytest recipe-creator-backend/tests/test_<area>.py -q`
- Frontend: `just test-frontend`

Database tests require a disposable real SurrealDB and `RECIPE_TEST_DB_URL` / `RECIPE_TEST_DB_PASSWORD`; skipped tests do not validate persistence. Run `just test` for broader coverage.

## Guardrails

Preserve authored prose and legacy IDs. Enforce ownership/privacy on the backend; never expose pending photos or secrets. Keep atomic writes in the repository transaction layer—its upstream compatibility fixes have real-DB regression tests. Migrations are explicit, never automatic at API startup.

After API schema changes, run `npm --prefix recipe-creator-frontend run generate:api`; do not hand-edit `src/api.generated.ts`. Keep dependencies locked, changes focused, and documentation brief.

## Commit policy

Every completed code, configuration, test, or documentation change MUST be committed before reporting the task complete, even when the user does not explicitly request a commit. Treat committing as the final required delivery step, not an optional cleanup step. Before the final response, verify that all files changed by this task are included in the commit and that no task-owned changes remain uncommitted. Never skip this step because validation passed or because the user did not mention version control. The only exception is an explicit user instruction not to commit.
