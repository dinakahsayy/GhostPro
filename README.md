# GhostPro — LinkedIn AI Ghostwriter

GhostPro learns a professional's voice and turns their real stories into LinkedIn
posts — generated, scheduled, and (optionally) auto-published on their behalf.

It's a Flask app with background scheduling, built phase-by-phase from an SDLC
plan. The full product loop works end to end:

> onboard → connect LinkedIn → drop content in the **Content Inbox** (or
> auto-discover it from **followed sources**) → **scheduled generation** in your
> voice → **2-hour preview** + notifications → approve / regenerate / edit /
> publish → **engagement sync** + **style refresh**.

## Features

- **LinkedIn OAuth + onboarding** — multi-step questionnaire builds a per-user
  style profile; tokens are **Fernet-encrypted at rest** and auto-refreshed.
- **Content Inbox** — drop in text notes, URLs (fetched + **SSRF-sandboxed**),
  quotes, or company updates; `post_soon` / `use_whenever` priority.
- **Followed sources** — RSS feeds / sites polled daily; new items surface as
  one-tap **suggestions**.
- **Generation pipeline (GPT-4o)** — source priority: inbox → topics → industry
  news (NewsAPI) → seasonal; writes in the user's voice within LinkedIn limits.
- **Scheduler** — per-user cadence (frequency / days / time / timezone); auto-post
  with a 2-hour preview window, or manual approval; publish retries with backoff.
- **Notifications** — in-app center (bell) + SendGrid email, with source labels.
- **Preview / approval** — approve, regenerate (with version history), edit,
  reschedule, discard, publish.
- **Dashboard** — calendar of upcoming/past posts, engagement analytics, manual
  LinkedIn metric sync.
- **Account** — settings + schedule pause/resume; **GDPR deletion**
  (soft-delete → 30-day grace → cascade purge).
- **Security** — CSRF protection, encrypted tokens, SSRF-guarded fetching.

## Tech stack

Flask · SQLAlchemy 2.x + Alembic · Flask-Login · Flask-WTF · APScheduler
(Celery + Redis optional) · OpenAI GPT-4o · SendGrid · feedparser · Jinja2 +
Alpine.js. SQLite in dev, PostgreSQL in production.

## Quick start

```bash
python -m venv .venv && source .venv/bin/activate
pip install -r requirements.txt
cp .env.example .env          # fill in keys (see below)
alembic upgrade head          # build the database
python main.py                # http://localhost:8080
```

In development, visit `/` and use **Dev login** (enabled when
`FLASK_ENV=development`) to explore without LinkedIn credentials.

### Required configuration

`SECRET_KEY` and `DATABASE_URL` are enough to boot. For full functionality add
`OPENAI_API_KEY`, the `LINKEDIN_*` OAuth values, and optionally
`SENDGRID_API_KEY` / `NEWSAPI_API_KEY` / `SENTRY_DSN`. Every integration
degrades gracefully when its key is absent. See `.env.example` for the full list.

## Tests

```bash
python -m pytest tests/
```

The suite (149 tests) runs against an isolated temp SQLite database — no
external services required.

## Background jobs

By default APScheduler runs the generation, publish, source-watch, and
account-purge ticks inside the web process. For scale, switch to Celery + Redis
(`GHOSTPRO_SCHEDULER=0` + the `worker`/`beat` processes). See
[DEPLOY.md](DEPLOY.md).

## Project layout

```
app/
  __init__.py          # app factory (login, CSRF, Sentry)
  routes.py            # all HTTP routes
  models/database.py   # SQLAlchemy models
  services/            # business logic (generation, scheduler, inbox, …)
  utils/               # crypto, SSRF-guarded fetch, time
  templates/           # Jinja2 + Alpine.js
migrations/            # Alembic
tests/                 # pytest
```

## Deployment

See [DEPLOY.md](DEPLOY.md) for Railway/Render setup, Postgres/Redis, migrations,
and the security checklist.
