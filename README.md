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
production use. TRACE currently runs as a local-first application, not a hosted SaaS.

## Setup (Windows)

Use a stable Python 3.10+ release. Pre-release Python builds are not supported. The
backend's local configuration is read from `qa-platform/.env`; if it does not exist,
copy `backend/.env.example` there and replace the example values:

```powershell
Copy-Item .\backend\.env.example .\qa-platform\.env
```

Generate a 64-character hexadecimal `QA_AI_ENCRYPTION_KEY` and keep it stable while
provider credentials are stored in the database.

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
default. Create the first account in the sign-in screen. For an existing installation,
the app continues to use `qa-platform/qa_platform.sqlite3` and
`qa-platform/storage/`. Set `QA_PLATFORM_DB` to override the SQLite path, or
`QA_PLATFORM_DATABASE_URL` to use PostgreSQL. Remote PostgreSQL URLs must enable TLS.

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
