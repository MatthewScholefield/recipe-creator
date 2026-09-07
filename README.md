# Recipe Creator

A small recipe notebook for family and friends. Browse without an account, publish recipes, connect your devices, and contribute photos. Keep recipes as plain text or organize ingredients for scaling and gram estimates.

## Run locally

Requires Python 3.12+, uv, Node.js 22+, npm, and a running SurrealDB 3.2.4 server.

```sh
make install
cp .env.example .env
```

Edit `.env` with local database connection details, then:

```sh
make migrate
make dev
```

Open `http://localhost:5173`. The API runs on port 8080. For local HTTP development, turn off secure cookies in `.env`; leave them enabled elsewhere.

AI is optional: add the provider key to `.env` to enable parsing and ingredient estimates. Recipes can still be shared as text without it. For administration, use `make hash-password` and add the resulting hash to `.env`.

`make help` lists build, import, backup, and restore commands. Production uses static frontend assets, FastAPI, and SurrealDB; configure HTTPS, secure cookies, allowed origins, and persistent storage in `.env` before deployment. Keep `.env` private and never commit it.
