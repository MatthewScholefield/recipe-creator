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

After API schema changes, run `bun run --cwd recipe-creator-frontend generate:api`; do not hand-edit `src/api.generated.ts`. Keep dependencies locked, changes focused, and documentation brief.

## ALWAYS COMMIT

**Before every final response after modifying repository files, commit all changes owned by the current task. The only exception is when the user explicitly asks not to commit.**

Use this exact delivery gate:

1. After implementation and validation pass, inspect `git status --short` and identify every change owned by this task.
2. Stage only those task-owned files; do not include unrelated user work.
3. Create the commit immediately, before writing any completion message.
4. Verify the commit contains every task-owned change and that no task-owned modifications remain uncommitted.
5. If any task-owned change remains, stop and commit it before reporting completion.

Never treat passing tests, a clean diff review, or the absence of an explicit commit request as a reason to skip this gate. The only exception is an explicit user instruction not to commit.
