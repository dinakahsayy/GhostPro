# Deploying GhostPro

GhostPro runs as a Flask web app plus background jobs. In development the jobs
run in-process via APScheduler; in production you can keep APScheduler or scale
out to Celery + Redis.

## 1. Provision services

- **PostgreSQL** — set `DATABASE_URL=postgresql://user:pass@host:5432/dbname`
- **Redis** (only if using Celery) — set `REDIS_URL=redis://host:6379/0`

## 2. Environment variables

Copy `.env.example` and fill in:

| Variable | Purpose |
|----------|---------|
| `SECRET_KEY` | Flask sessions + token-encryption key derivation (32-byte random) |
| `FERNET_KEY` | Optional dedicated key for LinkedIn token encryption |
| `DATABASE_URL` | Postgres connection string |
| `ANTHROPIC_API_KEY` | Post generation (Claude) |
| `LINKEDIN_CLIENT_ID` / `LINKEDIN_CLIENT_SECRET` / `LINKEDIN_REDIRECT_URI` | OAuth |
| `SENDGRID_API_KEY` / `NOTIFICATION_FROM_EMAIL` | Email notifications |
| `NEWSAPI_API_KEY` | Industry-news fallback source |
| `APP_BASE_URL` | Public URL used in notification links |
| `SENTRY_DSN` | Error monitoring (optional) |
| `REDIS_URL` / `CELERY_BROKER_URL` | Celery broker (optional) |
| `GHOSTPRO_SCHEDULER` | `1` (APScheduler in web process) or `0` (use Celery) |

## 3. Run database migrations

```bash
alembic upgrade head
```

The `release` line in the `Procfile` runs this automatically on Railway/Render.

## 4. Processes (Procfile)

- **web** — `gunicorn main:app` serves the app.
- **worker** / **beat** — Celery worker + scheduler, only if you choose the
  Celery path.

### Background jobs: pick ONE

- **APScheduler (default, simplest):** leave `GHOSTPRO_SCHEDULER=1`. The web
  process runs the generation, publish, source-watch, and account-purge ticks.
  Good up to a single web instance.
- **Celery + Redis (scale):** set `GHOSTPRO_SCHEDULER=0`, run the `worker` and
  `beat` processes. Queues — `generation`, `publishing`, `source_watch`,
  `maintenance` — can be scaled independently. Task bodies reuse the same
  service functions as the APScheduler ticks.

> Do not run both: with `GHOSTPRO_SCHEDULER=1` and Celery beat active, jobs
> would fire twice.

## 5. Railway / Render notes

- Point the start command at the `Procfile`, or set `web` to
  `gunicorn main:app --bind 0.0.0.0:$PORT`.
- Add the Postgres (and Redis) plugins and copy their URLs into the env vars.
- Set the LinkedIn redirect URI to `https://<your-domain>/linkedin/callback` and
  register it in the LinkedIn app.

## Security checklist (§11)

- Tokens are Fernet-encrypted at rest; keep `SECRET_KEY`/`FERNET_KEY` secret and
  stable (rotating them invalidates stored LinkedIn tokens).
- All mutating endpoints are CSRF-protected.
- Inbox URL fetching is SSRF-sandboxed (https-only, private IPs blocked).
- No tokens/PII are logged.
