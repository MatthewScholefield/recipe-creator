# Recipe Creator

A small recipe notebook for family and friends. Browse without an account, publish recipes, connect your devices, and contribute photos. Keep recipes as plain text or organize ingredients for scaling and gram estimates.

## Run locally

Requires Python 3.12+, uv, Node.js 22+, npm, the SurrealDB 3.2.4 CLI, and [just](https://just.systems/man/en/installation.html).

```sh
just install
cp .env.example .env
```

In one terminal, start the local embedded SurrealKV database:

```sh
just db
```

In a second terminal, apply migrations and start the application:

```sh
just migrate
just dev
```

Open `http://localhost:2772`. The API runs on port 2332 with one Uvicorn worker. For local HTTP development, turn off secure cookies in `.env`; leave them enabled elsewhere.

AI is optional: add the provider key to `.env` to enable parsing and ingredient estimates. Recipes can still be shared as text without it.

### Configure the admin password

From the repository root, run:

```sh
just hash-password
```

The command prompts for the password twice and prints the exact `RECIPE_ADMIN_PASSWORD_HASH=...` line to add to the root `.env` file. Restart the API after updating `.env`; log in at `/admin` with the original password. Keep `.env` private and never commit it.

`just help` lists build, import, backup, and restore commands. Maintenance recipes accept CLI arguments directly, for example `just import snapshot.json --dry-run`. Production uses static frontend assets, FastAPI, and SurrealDB; configure HTTPS, secure cookies, allowed origins, and persistent storage in `.env` before deployment. Keep `.env` private and never commit it.
