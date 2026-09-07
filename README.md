# Recipe Creator

A small recipe notebook for family and friends. Browse without an account, publish recipes, connect your devices, and contribute photos. Keep recipes as plain text or organize ingredients for scaling and gram estimates.

## Run locally

Requires Python 3.12+, uv, Node.js 22+, npm, and a running SurrealDB 3.2.4 server.

```sh
make install
export RECIPE_DB_URL=http://127.0.0.1:8000
export RECIPE_DB_USER=root
export RECIPE_SECURE_COOKIES=false
```

Set `RECIPE_DB_PASSWORD` privately to your database password, then:

```sh
make migrate
make dev
```

Open `http://localhost:5173`. The API runs on port 8080. Disabling secure cookies is for local HTTP development only.

AI is optional: configure `RECIPE_AI_API_KEY` to enable parsing and ingredient estimates. Recipes can still be shared as text without it. For administration, use `make hash-password` and set `RECIPE_ADMIN_PASSWORD_HASH` privately.

`make help` lists build, import, backup, and restore commands. Production uses static frontend assets, FastAPI, and SurrealDB; configure HTTPS, secure cookies, allowed origins, and persistent storage before deployment. Never commit passwords or API keys.
