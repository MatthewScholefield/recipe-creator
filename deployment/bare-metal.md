# Bare-metal production deployment

This is a reference layout for running Recipe Creator on one Linux host with systemd, nginx, SurrealDB, and blue/green application releases. Replace the example domain, user, paths, and secrets for the target machine.

The design keeps ordinary releases online while the inactive slot is built and warmed. Releases containing database migrations use a coordinated maintenance window: stop all application writers, create a verified backup, migrate once, start the new slot, and then switch nginx. Do not run migrations from API startup.

## Layout

```text
/srv/recipe-creator/
  blue/                         # Git checkout; `.env` links to shared config
  green/                        # Git checkout; `.env` links to shared config
  config/production.env         # Private shared configuration
  state/active-slot             # `blue` or `green`
  data/                         # SurrealKV data
  media/                        # Uploaded media
  backups/                      # Private pre-migration archives
/var/www/recipe-creator/releases/<slot>-<git-sha>/
/etc/nginx/snippets/recipe-creator-frontend.conf
/etc/nginx/snippets/recipe-creator-backend.conf
/usr/local/sbin/deploy-recipe-creator
```

Use a dedicated unprivileged account such as `recipe`. The deployment command needs narrowly scoped privilege to install frontend releases, manage the two backend units, validate/reload nginx, and run the migration maintenance sequence. A root-owned systemd oneshot is the simplest arrangement on a single-purpose host.

## Prerequisites

Install:

- Python 3.12 or newer and [uv](https://docs.astral.sh/uv/)
- Bun matching `recipe-creator-frontend/package.json`
- SurrealDB 3.2.4
- `git`, `just`, `curl`, nginx, and systemd
- A TLS certificate for the public hostname

Clone the public repository into both slots:

```sh
sudo -u recipe git clone https://github.com/MatthewScholefield/recipe-creator.git /srv/recipe-creator/blue
sudo -u recipe git clone https://github.com/MatthewScholefield/recipe-creator.git /srv/recipe-creator/green
```

Create one shared production environment file at `/srv/recipe-creator/config/production.env` with mode `0600`. At minimum, configure the following without committing its values:

```dotenv
RECIPE_DB_URL=http://127.0.0.1:8333
RECIPE_DB_NAMESPACE=recipe_creator
RECIPE_DB_DATABASE=recipe_creator
RECIPE_DB_USER=root
RECIPE_DB_PASSWORD=<random-database-password>
RECIPE_DB_PATH=/srv/recipe-creator/data
RECIPE_MEDIA_ROOT=/srv/recipe-creator/media

SURREAL_PATH=surrealkv:///srv/recipe-creator/data
SURREAL_BIND=127.0.0.1:8333
SURREAL_USER=root
SURREAL_PASS=<same-random-database-password>

RECIPE_SECURE_COOKIES=true
RECIPE_PUBLIC_ORIGIN=https://recipes.example.com
RECIPE_ALLOWED_ORIGINS=["https://recipes.example.com"]
RECIPE_SESSION_SECRET=<random-persistent-secret>
FORWARDED_ALLOW_IPS=127.0.0.1
WEB_CONCURRENCY=1

RECIPE_AI_BASE_URL=<provider-base-url>
RECIPE_AI_MODEL=<provider-model>
RECIPE_AI_API_KEY=<private-provider-key>
```

Link the file into both checkouts so CLI maintenance commands and systemd use the same configuration:

```sh
sudo -u recipe ln -s ../config/production.env /srv/recipe-creator/blue/.env
sudo -u recipe ln -s ../config/production.env /srv/recipe-creator/green/.env
```

Create the data, media, backup, release, and state directories before starting services. The application user must own the data, media, backup, checkout, and state paths. nginx only needs read access to frontend release directories.

## systemd services

Adjust binary paths if uv or SurrealDB are installed elsewhere.

### SurrealDB

`/etc/systemd/system/recipe-creator-db.service`:

```ini
[Unit]
Description=Recipe Creator SurrealDB
After=network.target

[Service]
Type=simple
User=recipe
Group=recipe
WorkingDirectory=/srv/recipe-creator
EnvironmentFile=/srv/recipe-creator/config/production.env
ExecStart=/usr/local/bin/surreal start --no-banner --bind ${SURREAL_BIND} ${SURREAL_PATH}
Restart=on-failure
RestartSec=5
TimeoutStopSec=30

[Install]
WantedBy=multi-user.target
```

### Blue backend

`/etc/systemd/system/recipe-creator-blue.service`:

```ini
[Unit]
Description=Recipe Creator blue backend
Requires=recipe-creator-db.service
After=network.target recipe-creator-db.service

[Service]
Type=simple
User=recipe
Group=recipe
WorkingDirectory=/srv/recipe-creator/blue
EnvironmentFile=/srv/recipe-creator/config/production.env
Environment=PATH=/usr/local/bin:/usr/bin:/bin
ExecStart=/usr/local/bin/uv run --project recipe-creator-backend python -m recipe_creator.server --host 127.0.0.1 --port 2332
Restart=on-failure
RestartSec=5
TimeoutStopSec=30

[Install]
WantedBy=multi-user.target
```

### Green backend

`/etc/systemd/system/recipe-creator-green.service` is identical except for:

```ini
[Unit]
Description=Recipe Creator green backend
Requires=recipe-creator-db.service
After=network.target recipe-creator-db.service

[Service]
Type=simple
User=recipe
Group=recipe
WorkingDirectory=/srv/recipe-creator/green
EnvironmentFile=/srv/recipe-creator/config/production.env
Environment=PATH=/usr/local/bin:/usr/bin:/bin
ExecStart=/usr/local/bin/uv run --project recipe-creator-backend python -m recipe_creator.server --host 127.0.0.1 --port 2333
Restart=on-failure
RestartSec=5
TimeoutStopSec=30

[Install]
WantedBy=multi-user.target
```

Only the active backend slot should be enabled after a successful cutover. Both may run briefly against the same database during a non-migration deployment.

## nginx

Keep the active release and backend target in small generated snippets.

`/etc/nginx/snippets/recipe-creator-frontend.conf`:

```nginx
root /var/www/recipe-creator/releases/blue-<git-sha>;
```

`/etc/nginx/snippets/recipe-creator-backend.conf`:

```nginx
proxy_pass http://127.0.0.1:2332;
```

Example HTTPS site:

```nginx
server {
    listen 443 ssl;
    listen [::]:443 ssl;
    server_name recipes.example.com;

    include /etc/nginx/snippets/recipe-creator-frontend.conf;
    index index.html;

    # Configure certificate paths using the host's ACME/TLS tooling.

    client_max_body_size 1m;

    location /api/ {
        include /etc/nginx/snippets/recipe-creator-backend.conf;
        proxy_http_version 1.1;
        proxy_set_header Host $host;
        proxy_set_header X-Real-IP $remote_addr;
        # Replace forwarded addresses; never append an untrusted client value.
        proxy_set_header X-Forwarded-For $remote_addr;
        proxy_set_header X-Forwarded-Proto $scheme;
    }

    location /assets/ {
        try_files $uri =404;
        add_header Cache-Control "public, max-age=31536000, immutable";
    }

    location / {
        try_files $uri $uri/ /index.html;
    }
}

server {
    listen 80;
    listen [::]:80;
    server_name recipes.example.com;
    return 301 https://$host$request_uri;
}
```

Expose only nginx publicly. SurrealDB and both backend ports must remain loopback-only. Set `FORWARDED_ALLOW_IPS` to the exact reverse-proxy address or CIDR, never `*`.

## Deployment algorithm

A root-owned `/usr/local/sbin/deploy-recipe-creator` command should use `flock` so only one deployment runs at a time, then perform these steps:

1. Read `state/active-slot`; derive the inactive checkout, backend unit, and port.
2. Fetch `origin/main` in the inactive checkout. Exit successfully if its target commit already matches the active commit.
3. Record whether `recipe-creator-backend/migrations/` differs between the active and target commits.
4. Reset only the inactive deployment checkout to the target commit and ensure its `.env` symlink still targets the shared production configuration.
5. In the inactive checkout, run `just install`, `just check`, and `just build` as the application user.
6. Copy `recipe-creator-frontend/dist/` to a new immutable release directory named with the slot and commit SHA.
7. Continue with the ordinary or migration path below.

### Ordinary blue/green path

1. Enable and start the inactive backend.
2. Poll its loopback `/api/health/ready` endpoint until healthy; abort without changing nginx if it does not become ready.
3. Write candidate frontend and backend nginx snippets, preserving the previous snippets for rollback.
4. Run `nginx -t`, gracefully reload nginx, and verify `/api/health/ready` through the public origin.
5. If public verification fails, restore both snippets, reload nginx, and stop the candidate backend.
6. On success, disable and stop the old backend, atomically update `state/active-slot`, and retain the prior static release for rollback.

The candidate backend is warm before nginx switches, and an nginx reload keeps existing connections on old workers. This makes ordinary deployments effectively zero-downtime.

### Migration path

A migration release cannot safely use the overlapping-writer step:

1. Finish all install, check, build, and static staging work before downtime begins.
2. Stop the active backend and every other API, job runner, importer, or database writer. Keep SurrealDB running.
3. Run `just backup <private-archive> --confirm-quiesced --surreal-binary <surreal-path>` from the active release. Require success before continuing.
4. Run `just migrate` once from the new inactive release.
5. Start only the new backend and require its loopback readiness check.
6. Switch and validate nginx as above, then update the active-slot state.

If backup creation fails, restart the old backend because no schema change occurred. If migration starts and then fails, keep application writers stopped for operator recovery. Do not start old code against a possibly migrated database. Restore requires the verified pre-migration archive and the explicit restore procedure described in the main README.

This migration path is automated but intentionally incurs downtime for backup, migration, and new-backend startup.

## Automatic timer

`/etc/systemd/system/recipe-creator-auto-deploy.service`:

```ini
[Unit]
Description=Deploy latest Recipe Creator production release
After=network-online.target recipe-creator-db.service
Wants=network-online.target

[Service]
Type=oneshot
ExecStart=/usr/local/sbin/deploy-recipe-creator
TimeoutStartSec=45min
```

`/etc/systemd/system/recipe-creator-auto-deploy.timer`:

```ini
[Unit]
Description=Check daily for Recipe Creator production updates

[Timer]
OnCalendar=*-*-* 03:30:00
RandomizedDelaySec=20m
Persistent=true
Unit=recipe-creator-auto-deploy.service

[Install]
WantedBy=timers.target
```

Enable the database, initial active backend, nginx site, and timer:

```sh
systemctl daemon-reload
systemctl enable --now recipe-creator-db.service
systemctl enable --now recipe-creator-blue.service
systemctl enable --now recipe-creator-auto-deploy.timer
nginx -t && systemctl reload nginx
```

Useful checks:

```sh
systemctl list-timers recipe-creator-auto-deploy.timer
systemctl start recipe-creator-auto-deploy.service
journalctl -u recipe-creator-auto-deploy.service
curl --fail https://recipes.example.com/api/health/ready
```

## Operational requirements

- Keep `config/production.env`, database data, media, and backup archives private and out of Git.
- Copy verified backups off-host and test restoration separately.
- Monitor disk use, failed timers, SurrealDB health, and cleanup failures.
- Preserve at least the previous frontend release and pre-migration backup.
- Treat a migration failure as an operator incident; never hide it by pointing old application code at the changed database.
- Grant administrator access with `just admin-users` and `just admin-grant`; do not introduce a shared admin password.
