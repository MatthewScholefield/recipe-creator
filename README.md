# Recipe Creator

A small recipe notebook for family and friends. Browse without an account, publish recipes, connect your devices, and contribute photos. Keep recipes as plain text or organize ingredients for scaling and gram estimates.

## Run locally

Requires Python 3.12+, uv, Bun 1.4+, the SurrealDB 3.2.4 CLI, and [just](https://just.systems/man/en/installation.html).

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

## Production deployment

Migration `0004_recipe_views` changes the stored user trust field and creates the view-tracking tables. Use a maintenance window; old and new application processes must not write the database at the same time:

1. Stop every API process, job runner, importer, and other database writer.
2. Create and verify a backup, for example `just backup backup.tar --confirm-quiesced`.
3. Run `just migrate` once.
4. Start only the updated API and workers, then check `/api/health/ready`.

Do not run migrations automatically at application startup. Do not use a rolling deployment across this migration.

### Reverse-proxy boundary

View-abuse limits use the client address accepted by Uvicorn. In production, expose the API only through the HTTPS reverse proxy, have the proxy replace—not append to—forwarded headers, and set `FORWARDED_ALLOW_IPS` to that proxy's exact IP address or CIDR. Never use `FORWARDED_ALLOW_IPS=*`, and do not leave a direct path to the Uvicorn socket. For a directly exposed or local API, start it with `--no-proxy-headers`.

After deployment, verify through the public origin that two external client addresses are recorded as distinct IP windows and that a forged `X-Forwarded-For` sent outside the trusted proxy cannot select the tracking address.

### View-tracking storage

Anonymous viewer credentials and per-IP abuse windows expire and are removed by the hourly cleanup job. Recipe/viewer membership rows deliberately remain so a returning viewer is never counted as unique twice. Annual membership growth is `recipes × new unique viewers per recipe per day × 365`; for example, 100 recipes receiving 50 new viewers each day add about 1,825,000 membership rows per year. Monitor database size and cleanup failures before storage pressure reaches the host's limit.
