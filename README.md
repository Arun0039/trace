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
`DATABASE_URL`, `QA_PLATFORM_DATABASE_URL`, `POSTGRES_URL`, or `POSTGRESQL_URI` to use PostgreSQL.
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

### Cloudflare Pages deployment

The frontend is static HTML, CSS, and vanilla JavaScript; it does not use a
framework preset or package install. Create a Cloudflare Pages project with:

| Setting | Value |
|---|---|
| Root directory | `frontend` |
| Framework preset | None |
| Build command | `node build_pages.mjs` |
| Build output directory | `dist` |
| Node.js version | `22` |
| Build environment variable | `QA_API_BASE_URL=https://api.example.com` |

Use the HTTPS origin of the Render API for `QA_API_BASE_URL` (no path or trailing
API route). The build validates the origin, generates `config.js`, and copies the
static app and SPA route rewrites into `dist`. This URL is public configuration,
not a secret. Do not put credentials or keys in Cloudflare Pages variables.

For authentication, assign custom domains under the same registrable domain to
both providers, for example `app.example.com` for Pages and `api.example.com` for
Render. TRACE uses `SameSite=Strict` session cookies; the default `pages.dev` and
`onrender.com` hostnames are cross-site, so browsers will not send those cookies
between them. Configure the Render service's `QA_CORS_ORIGINS` to the exact Pages
origin, such as `https://app.example.com`. Do not use `*` with credentialed
requests. Cloudflare preview URLs are not allowed by default; add only specific
trusted preview origins if needed.

The build also generates Cloudflare security headers and a no-store policy for
`config.js`. Its `_redirects` file maps `/login`, `/signup`, `/workspace`, and
other single-page routes to the frontend entry point and maps the Product Owner
and Developer pages to their static HTML files. Test direct navigation and
refreshes after configuring the Pages custom domain.

### Vercel frontend deployment

Vercel can host the static frontend; keep the backend on Render. The root
[vercel.json](./vercel.json) selects the "Other" framework preset, builds
`frontend/dist`, and applies the SPA rewrites and security headers that Vercel
does not read from Cloudflare's `_redirects` and `_headers` files.

| Setting | Value |
|---|---|
| Root directory | blank (repository root) |
| Framework preset | Other (enforced by `vercel.json`) |
| Build command | `node frontend/build_pages.mjs` |
| Build output directory | `frontend/dist` |
| Build environment variable | `QA_API_BASE_URL=https://api.example.com` |

Set the Vercel production origin as `QA_CORS_ORIGINS` on the Render service.
Vercel's default `*.vercel.app` hostname is cross-site with `*.onrender.com`, so
session cookies require custom domains under the same registrable domain. Do not
deploy the repository root as a Python app on Vercel: the backend is a
long-running HTTP server with a persistent disk and is not compatible with Vercel
Functions.

### Render deployment

The root [render.yaml](./render.yaml) defines the `qa-tracker` web service and
the `qa-tracker-trial-expiry` Cron Job on Render's native Python runtime. Both
build from the repository root with `pip install -r requirements.txt` and pin
Python 3.11.9 through [.python-version](./.python-version). The Blueprint
configures a paid single-instance service with a 1 GB persistent disk mounted at
`/opt/render/project/src/qa-platform/storage`; increase disk capacity based on
measured usage. Database records live in Render PostgreSQL, configured through
`DATABASE_URL`. The blueprint leaves that value unset so the Render PostgreSQL
external URL can be supplied with TLS enabled.

The backend exposes `GET /health` (also `/healthz`) as a liveness check; it returns
HTTP 200 with `{"status":"ok"}` without connecting to the database. `GET /ready`
checks database connectivity and is configured as the Render health check. Both
are public and do not reveal connection details.

The web service starts `python app.py` from the repository root. The root
`requirements.txt` and `app.py` files are shims that delegate to `backend/`; the
backend itself remains the canonical application directory. For local setup,
install dependencies with `pip install -r requirements.txt` from the repository
root, or `pip install -r requirements.txt` from `backend/`. If you create the
Render service manually instead of using the Blueprint, leave its root directory
blank (repository root) and use the same build and start commands. Do not set the
root directory to a package subfolder such as `backend/qa_platform`, because
`requirements.txt` will not be found there.

The trial-expiry Cron Job runs `python backend/expire_trials.py` from the
repository root every 10 minutes and requires the same TLS-enabled
`DATABASE_URL` as the web service. It processes
trials in batches and does not expose a public expiry endpoint. Request-time backend
authorization denies access at the exact trial-expiry timestamp even if the Cron Job
is delayed or unavailable. The web service and Cron Job must use the same
PostgreSQL database and `QA_TRIAL_DURATION_DAYS` setting. Keep the service at one
instance: rate limits and uploaded files are not shared across instances.

Render environment configuration in the Blueprint:

| Variable | Configured on | Purpose |
|---|---|---|
| `DATABASE_URL` | Web service, required | PostgreSQL connection URL with TLS (`sslmode=require` or stronger); configure in Render and keep it secret |
| `DATABASE_URL` | Trial-expiry Cron Job, required | The same PostgreSQL database URL used by the web service |
| `QA_CORS_ORIGINS` | Web service, required | Exact Cloudflare Pages production origin, e.g. `https://app.example.com` |
| `QA_AI_ENCRYPTION_KEY` | Web service | Required when `QA_ENVIRONMENT` is `production`/`prod`/`live`; must be a 64-character hexadecimal key. Set it manually in Render (`sync: false`); keep it stable to decrypt saved provider credentials. Generate one with `python -c "import secrets; print(secrets.token_hex(32))"` |
| `QA_ADMIN_PASSWORD` | Web service, required for Product Owner access | Bootstrap administrator password; use a unique secret of at least 12 characters |
| `QA_TRIAL_DURATION_DAYS` | Web service and expiry job | Trial length in days (1–365; default 14); set consistently on both processes |
| `HOST` | Web service | Set to `0.0.0.0`; Render supplies `PORT` at runtime |
| `QA_ENVIRONMENT` | Web service | Set to `production` for structured production logs |
| `QA_COOKIE_SECURE` | Web service | `true` for HTTPS session cookies |
| `QA_ALLOW_PUBLIC_AUTH` | Web service | `true` enables public account registration; change it if registration should be restricted |

#### Deployment, migration, smoke test, and rollback

1. Push the reviewed repository to GitHub, then create the Render services from
   the root `render.yaml`. Create a Render PostgreSQL database in the same region
   and set its TLS-enabled external URL as `DATABASE_URL` on both the web service
   and trial-expiry job.
2. Set `QA_CORS_ORIGINS`, `QA_ADMIN_PASSWORD`, and `QA_AI_ENCRYPTION_KEY` on the
   web service. Set `QA_TRIAL_DURATION_DAYS` identically on the web service and
   Cron Job. Attach the custom API domain, such as `api.example.com`.
3. Create the Cloudflare Pages project from the same GitHub repository using the
   settings above. Set `QA_API_BASE_URL` to the Render API origin, attach the
   Pages domain (such as `app.example.com`), and ensure the two domains share the
   same registrable domain.
4. Render installs `requirements.txt` and starts `python app.py` from the
   repository root. `initialize()` applies the existing additive schema upgrades
   at startup; it does not reset or drop the database. For an existing SQLite
   installation, migrate a copy to an empty PostgreSQL database with
   `py -3 migrate_sqlite_to_postgres.py` from `backend/` before pointing
   production at it.
5. After both deployments report healthy, verify:

   ```sh
   curl --fail https://api.example.com/health
   curl --fail https://api.example.com/ready
   ```

   Then use the Pages site to create an account, sign in, refresh `/workspace`,
   and verify a document upload/download. These browser checks confirm cookie,
   CORS, and persistent-file behavior that the HTTP health probes cannot verify.
6. To roll back application code, select the previous successful deployment in
   Render and redeploy the previous Pages commit. The schema upgrades are
   additive, so leave the database in place during an application rollback.
   Restore a database or files only from a verified backup and during a planned
   recovery; see [SELF_HOSTING.md](./SELF_HOSTING.md) for the restore command.

Keep `sslmode=require` or stronger in the PostgreSQL URL. Set the Pages API
origin and exact Pages CORS origin before testing signup/login. Render's paid
service and disk incur charges; confirm current plan and disk pricing before
creating resources. Local runs need no Cron Job: `/health` and `/ready` are
available on the backend's default port.

The app stores uploaded files and archives on its local filesystem under
`qa-platform/storage/`; the Render disk preserves that directory across service
restarts and deployments. The disk is instance-local and is not shared across
multiple app instances. Configure backups and test restores before storing
customer data.

The Blueprint enables public registration. Review access, password recovery
(which does not send email), session, privacy, and terms requirements before
exposing a production service. Keep the database URL and any provider credentials
out of source control; retain the same `QA_AI_ENCRYPTION_KEY` across deploys.

The web service refuses to start in a live environment (`QA_ENVIRONMENT` set to
`production`, `prod`, or `live`) unless `QA_AI_ENCRYPTION_KEY` is a 64-character
hexadecimal key. Render's Blueprint `generateValue` produces a base64 secret, which
is not valid for this setting, so provide the key manually or the service exits at
startup with `QA_AI_ENCRYPTION_KEY must be set to a 64-character hexadecimal key.`

### Workspace signup, trials, migration, and rollback

Each successful signup creates one named workspace, assigns the registering user
the tenant-scoped **Workspace Owner** role, and creates a configurable UTC free trial
(14 days by default) in the same database transaction. Workspace owners can manage members and QA records
inside their tenant; they are not platform administrators. A trial ends at its
stored exclusive expiry timestamp. The scheduled job marks it expired and suspends
the workspace without deleting database records, uploads, or archives. Login,
logout, and profile access remain available after suspension.

Workspace 001 (tenant ID 1) is the permanent no-payment workspace. It is excluded
from trial expiry and startup repairs its status to active if an earlier trial
configuration suspended it. Public signup creates later workspace IDs, which
receive the configured trial.

The additive startup migration creates `tenant_subscriptions`, adds workspace owner
and suspension metadata, and creates an active `legacy` subscription for each
pre-existing tenant that has no subscription row. Existing projects and files remain
in place; no legacy project is reassigned. Before deploying a new version, stop the
local app and back up `qa-platform/qa_platform.sqlite3` and `qa-platform/storage/`.
For hosted PostgreSQL, take a database snapshot/backup and verify it before rollout.
Keep the backup until signup, migration, trial access, and scheduled expiry are
verified.

Rollback does not require dropping the additive schema. If expiry has already
suspended a workspace, use the authorized system-admin reactivation operation
(`POST /api/workspaces/{workspace_id}/subscription/reactivate`) before reverting
the application, or restore the pre-deployment database backup. This operation
starts a new configured-length trial and records an audit event. The current platform has no
payment processor; paid-subscription billing and conversion remain out of scope.
For local verification, run `py -3 expire_trials.py` from `backend/`.

### Product Owner Operations

The separate, no-index operations dashboard is served at `/product-owner`. It uses
the configured bootstrap `QA_ADMIN_PASSWORD` and the existing HttpOnly administrator
session; ordinary workspace accounts are denied by every `/api/product-owner/*`
route. Provision access by setting a unique password of at least 12 characters in
`qa-platform/.env` and restarting the backend. Do not share that credential or grant
ordinary workspace roles system-wide access.

The versioned `0001_product_owner_dashboard` migration runs from `app.initialize()`.
It backfills immutable public user, tenant, and workspace UUIDs without changing
existing internal integer foreign keys, adds manual plan configuration and usage
events, and stores Product Owner actions in a separate append-only administrative
audit table. Existing tenant/workspace records and customer files remain in place.
Startup creates no new background job: the existing `expire_trials.py` scheduled
job continues to perform idempotent trial suspension, and request-time checks remain
the access-control boundary.

The dashboard offers tenant search/filter/sort/pagination, lifecycle and storage
details, manual plan assignment, suspension/reactivation, product usage, application
errors/health, and merged audit history. Customer API usage events store only a
tenant ID, normalized route, status, and timestamp; business content and request
bodies are not recorded. API request-rate analytics are still process-local and
reset on restart. Database storage is a stated estimate where the underlying engine
does not expose tenant-level page allocation. Cleanup is tenant-inventoried and
requires an exact tenant-specific confirmation, a reason, and audit logging; system
configuration and protected data are not cleanup targets.

`QA_TRIAL_DURATION_DAYS` defaults to 14 and accepts 1–365 days. Configure the same
value on the web process and trial-expiry job. To roll back the Product Owner schema,
first stop the service and back up the database (and storage), then run from
`backend/`:

```powershell
py -3 rollback_product_owner_migration.py --confirm remove-product-owner-dashboard-data
```

This explicit rollback preserves customer users, tenants, workspaces, and QA records,
but removes Product Owner usage events, storage history, plans, public ID columns,
and administrative audit records. Prefer rolling back the application while leaving the additive schema
in place when audit retention is required.

### Workspace storage and developer monitoring

The authenticated workspace has a Storage page at `/workspace/storage`. The
`storage.view` and `storage.delete` permissions are enforced by the API. It lists
workspace-owned project files and supported project record groups, with type,
project, keyword, and last-updated date filters. Filesystem files and binary
attachments/evidence use exact byte sizes. Structured database records use a
consistent approximation: character counts of selected text fields plus 64 bytes
per row. This is not a database-page or index-size measurement; the page identifies
these values as estimates. The total and optional quota combine exact file/blob
bytes with that record estimate.

Users with `storage.delete` may delete eligible file items and all records in a
selected project/type group (requirements, test cases, test runs, or defects).
Related form data and execution evidence are removed with their parent records in
the database transaction, and each action is audited. Project records and analysis
job records are protected on this page; delete a project through its project
management action, which archives its files. The storage estimate is not a
transactional database page-size measurement.

Developer monitoring is served separately at `/developer`; it uses the existing
`/login` session and requires the explicit `developer.view` permission for every
API request. Status changes require `developer.manage`; deleting error entries
requires `developer.delete`. Error records are stored in the application database,
separately from workspace storage accounting, and are redacted before persistence
and display. Frontend/API report ingestion is limited to 64 KB per request and 30
reports per client IP per minute. Resolved and ignored errors older than the
retention period are pruned. Configure:

| Variable | Purpose |
|---|---|
| `QA_STORAGE_QUOTA_MB` | Optional positive integer workspace quota in MiB |
| `QA_DEVELOPER_ERROR_RETENTION_DAYS` | Retention for resolved/ignored errors; integer from 1 to 3650, default 90 |
| `QA_ENVIRONMENT` | Environment label included in structured logs/error metadata; defaults to `development` |

Developer API-response counts are tenant-scoped, in-memory aggregates of up to
20,000 recent authenticated API responses. They reset when the server restarts and
are not durable audit or request logs. The developer health page reports database,
filesystem, and analysis-job status; it is not a substitute for deployment-level
monitoring or durable log retention.

### Optional AI provider dependencies

Local deterministic workflows do not require Node.js. For Google Gemini, OpenAI,
Anthropic Claude, or another SDK-backed AI provider, install Node.js 22+ and install
the locked dependencies from the backend directory:

```powershell
cd backend
npm ci
```

Ollama additionally requires its local server and selected model to be running, and is
available only in the Default Workspace. Each workspace has its own AI settings; a
workspace administrator enables the AI module, then any signed-in member of that
workspace can configure a supported provider under **Settings → AI Module**. Provider
keys are encrypted in SQLite using `QA_AI_ENCRYPTION_KEY`.

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
