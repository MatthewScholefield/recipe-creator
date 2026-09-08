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

### Grant administrator permission

Administrator access is granted to an existing user profile; there is no admin password or password-hash setup. With the database running, apply migrations explicitly before using the permission commands:

```sh
just migrate
just admin-users
just admin-grant --user-id <user-id> --yes
```

`admin-users` lists active, unmerged profiles without credentials. Omit `--user-id` from `admin-grant` to select a profile interactively; noninteractive use requires both `--user-id` and `--yes`. To revoke permission, run `just admin-revoke --user-id <user-id>` and confirm the requested user ID. Keep `.env` private and never commit it.

`just help` lists build, import, backup, and restore commands. Maintenance recipes accept CLI arguments directly, for example `just import snapshot.json --dry-run`. Production uses static frontend assets, FastAPI, and SurrealDB; configure HTTPS, secure cookies, allowed origins, and persistent storage in `.env` before deployment. Keep `.env` private and never commit it.
