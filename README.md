# Trace QA Platform

Trace is a local-first QA workspace for project-scoped requirements, test cases,
execution tracking, defect tracking, coverage, CSV reporting, BRD analysis, and
optional AI-assisted workflows. The browser UI remains vanilla JavaScript; the API
and business logic remain Python.

## Repository layout

```text
frontend/              Static browser application and independent development server
backend/               Python application, API, tests, and optional Node AI adapter
docs/                  Architecture and migration documentation
qa-platform/            Existing local database, .env, uploads, archives, and runtime data
```

Runtime data stays in `qa-platform/` so existing databases, secrets, uploads, and
archives are not moved. Keep this folder on persistent storage and do not commit its
`.env` file.

The public TRACE pages are served at `/`, `/about`, `/privacy`, and `/terms`.
Sign-in and account creation remain on the existing `/login` and `/signup` flows;
the authenticated workspace is available at `/workspace`. The privacy and terms
pages are drafts with deployment and legal placeholders that must be reviewed before
public launch. TRACE is a local-first application; the Render Blueprint is an
optional single-instance deployment, not a managed SaaS platform.

## Setup (Windows)

Use a stable Python 3.10+ release. Pre-release Python builds are not supported. The
backend's local configuration is read from `qa-platform/.env`; if it does not exist,
copy `backend/.env.example` there and replace the example values:

```powershell
Copy-Item .\backend\.env.example .\qa-platform\.env
```

Generate a 64-character hexadecimal `QA_AI_ENCRYPTION_KEY` and keep it stable while
provider credentials are stored in the database. Generate one with
`py -3 -c "import secrets; print(secrets.token_hex(32))"` and put it in
`qa-platform/.env`.

From the repository root, install the Python dependencies and start the backend:

```powershell
cd backend
py -3 -m pip install -r requirements.txt
py -3 app.py
```

Or use the Windows launcher, which finds a stable Python installation and installs
missing Python dependencies:

```powershell
.\backend\start-qa-platform.bat
```

The backend serves the frontend on the same origin at `http://127.0.0.1:8001/` by
default. Create an account in the sign-in screen. For an existing installation,
the app continues to use `qa-platform/qa_platform.sqlite3` and
`qa-platform/storage/`. Set `QA_PLATFORM_DB` to override the SQLite path, or set
`DATABASE_URL`, `QA_PLATFORM_DATABASE_URL`, or `POSTGRES_URL` to use PostgreSQL.
Remote PostgreSQL URLs must include `sslmode=require`, `verify-ca`, or `verify-full`.

### Run the frontend separately

The frontend has a standalone static development server. Start the backend with the
frontend origin explicitly allowed (or add the same value to `qa-platform/.env`):

```powershell
cd backend
$env:QA_CORS_ORIGINS = "http://127.0.0.1:3000"
py -3 app.py
```

In another terminal, start the frontend and set its API URL:

```powershell
cd frontend
$env:QA_API_BASE_URL = "http://127.0.0.1:8001"
py -3 dev_server.py
```

Open `http://127.0.0.1:3000/`. `QA_CORS_ORIGINS` is an exact, comma-separated origin
allowlist; do not use `*` when credentials are enabled. The frontend and backend
remain independently startable, and same-origin serving remains the default.

### Render deployment and scheduled health check

The root [render.yaml](./render.yaml) defines the `qa-tracker` web service and a
`qa-tracker-health-check` Cron Job. The backend already exposes `GET /health`
(also `/healthz`) without authentication. It returns HTTP 200 with
`{"status":"ok"}` and does not access the database or modify application data.
Health probes are excluded from the backend's routine access log.

The Blueprint uses the root Dockerfile, which installs
`backend/requirements.txt` directly. A root `requirements.txt` is also provided as
a compatibility entry point for deployment platforms configured to run
`pip install -r requirements.txt`; it includes the backend's dependency list.

The Cron Job uses the schedule `*/10 * * * *` (every 10 minutes, UTC) and
requests the URL in `QA_HEALTH_CHECK_URL`. Supply that URL when configuring the
Blueprint; with the default web-service name it is
`https://qa-tracker.onrender.com/health` (replace the hostname if you configure a
custom public domain). The check fails on non-2xx responses or a 90-second timeout and
does not print response bodies on success; this allows for Render's typical
roughly one-minute free-service cold start.

The Cron Job requires a paid Render compute plan (`starter` in the Blueprint; check
Render's current pricing before deploying it). The web service is currently
configured as `free`. Free web services can spin down after 15 minutes without
inbound traffic and may take about a minute to start on the next request. The
10-minute check is intended to keep it active during normal operation, but it
cannot guarantee uninterrupted availability: a delayed, failed, or suspended
Cron Job can allow a spin-down, and cold starts can still occur. Free web
services also have a monthly 750-hour workspace limit and ephemeral local
storage; this deployment uses PostgreSQL via `DATABASE_URL` for durable
database data. Use a paid web-service plan when predictable availability and
persistent-disk options are required.

Render environment configuration in the Blueprint:

| Variable | Configured on | Purpose |
|---|---|---|
| `QA_HEALTH_CHECK_URL` | Cron Job, required | Full public URL ending in `/health`; set after the web service URL is known |
| `DATABASE_URL` | Web service, required | PostgreSQL connection URL with TLS (`sslmode=require` or stronger); configure in Render and keep it secret |
| `QA_AI_ENCRYPTION_KEY` | Web service | Generated by Render; keep it stable to decrypt saved provider credentials |
| `HOST`, `PORT`, `QA_HOST`, `QA_PORT` | Web service | `HOST`/`PORT` bind to `0.0.0.0` and Render's service port; `QA_HOST`/`QA_PORT` are redundant aliases in the current Blueprint |
| `QA_ALLOW_PUBLIC_AUTH` | Web service | `true` enables public account registration; change it if registration should be restricted |

`QA_HEALTH_CHECK_URL` is intentionally not hard-coded because Render assigns or
allows custom public hostnames. Local runs need no Cron Job or extra variable:
the same endpoint is available at `http://127.0.0.1:8001/health` with the
default backend configuration.

The Blueprint also sets `FILE_STORAGE=local`, but the application currently uses
local filesystem storage unconditionally; this variable does not configure durable
storage.

The app stores uploaded files and archives on its local filesystem under
`qa-platform/storage/`. Render's free service filesystem is ephemeral; the
Blueprint does not configure durable file storage. Do not rely on free-tier
filesystem data for production. Before storing real uploads, use a paid service
with a persistent disk mounted at `/app/qa-platform/storage`, and configure backups.
The current application uses local filesystem storage and is not ready for
multi-instance deployment without shared durable file storage.

The Blueprint enables public registration. Review access, password recovery
(which does not send email), session, privacy, and terms requirements before
exposing a production service. Keep the database URL and any provider credentials
out of source control; retain the generated `QA_AI_ENCRYPTION_KEY` across deploys.

### Optional AI provider dependencies

Local deterministic workflows do not require Node.js. For Google Gemini or another
SDK-backed AI provider, install Node.js 22+ and install the locked dependencies from
the backend directory:

```powershell
cd backend
npm ci
```

Ollama additionally requires its local server and selected model to be running.
Configure providers after signing in under **Settings → AI Module**. Provider keys
are encrypted in SQLite using `QA_AI_ENCRYPTION_KEY`.

### Tests

Run the Python suite from `backend/`:

```powershell
py -3 -m unittest discover -s tests
```

Run the Node adapter tests when Node.js dependencies are installed:

```powershell
cd backend
npm test
```

See [backend/README.md](backend/README.md) for the application workflows and
[docs/ARCHITECTURE.md](docs/ARCHITECTURE.md) for the code and data layout.
