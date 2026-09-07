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

Run this from the repository root:

```sh
just hash-password
```

Enter the password twice. It must be at least 12 characters. The command only prints an Argon2id hash; it does not edit `.env` or reload a running API. Copy only the final line beginning with `$argon2id$` (not the prompts) and put it on one line in the root `.env` file:

```dotenv
RECIPE_ADMIN_PASSWORD_HASH=<paste the complete hash printed above>
```

Replace the placeholder with the complete hash. Keep the `RECIPE_` prefix and do not put the plaintext password or the hash command in `.env`. Restart the API after changing `.env` (`just dev` or `just dev-api`) because settings are loaded at startup. Log in at `/admin` with the original plaintext password. If the UI says admin login is not configured, check that the variable is spelled exactly as shown, that `.env` is in the repository root, and that the API was restarted.

`just help` lists build, import, backup, and restore commands. Maintenance recipes accept CLI arguments directly, for example `just import snapshot.json --dry-run`. Production uses static frontend assets, FastAPI, and SurrealDB; configure HTTPS, secure cookies, allowed origins, and persistent storage in `.env` before deployment. Keep `.env` private and never commit it.
