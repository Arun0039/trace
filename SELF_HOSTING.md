# Self-Hosting QA Tracker

QA Tracker is now fully self-hostable. You can run it on any server (Linux VPS, NAS, home server) with Docker or directly with Python.

## Option 1: Docker (Recommended)

### Prerequisites
- Docker and Docker Compose installed
- Git (to clone/pull updates)

### Setup
```bash
# Clone the repo
git clone <your-repo-url> qa-tracker
cd qa-tracker

# Copy environment file
cp .env.example .env

# Generate encryption key and update .env
python3 -c "import secrets; print(secrets.token_hex(32))" >> /dev/null  # just to remind
# Edit .env - set QA_AI_ENCRYPTION_KEY and other values
nano .env

# Start
docker compose up -d --build
```

The app will be available at `http://YOUR_SERVER_IP:8001`. For production, put Nginx/Traefik in front with HTTPS.

### Data Persistence
All data is stored in `./qa-platform/` which is mounted as a volume. Back up this entire directory regularly.

## Option 2: Direct Python (No Docker)

### Prerequisites
- Python 3.10+ (3.11 recommended)
- Linux, macOS, or Windows

### Setup
```bash
# Clone repo
git clone <your-repo-url> qa-tracker
cd qa-tracker

# Setup backend
cd backend
python3 -m venv .venv
source .venv/bin/activate  # Windows: .venv\Scripts\activate
pip install -r requirements.txt

# Setup env
cp ../.env.example ../qa-platform/.env
# Edit qa-platform/.env with your settings

# Run
python app.py
```

App runs on `http://127.0.0.1:8001` by default. Set `HOST=0.0.0.0` in `qa-platform/.env` for network access.

### Run as systemd service (Linux)
See the systemd unit example in README or create one pointing to your venv python and backend/app.py.

## Environment Variables

| Variable | Default | Notes |
|---|---|---|
| HOST / QA_HOST | 127.0.0.1 | Bind to 0.0.0.0 for external access |
| PORT / QA_PORT | 8001 | Port to listen on |
| DATABASE_URL / QA_PLATFORM_DATABASE_URL / POSTGRES_URL / POSTGRESQL_URI | (unset) | Use PostgreSQL if set; remote URLs must use TLS (`sslmode=require` or stronger) |
| QA_ADMIN_PASSWORD | (unset) | Bootstrap Product Owner password; use a unique secret of at least 12 characters |
| QA_AI_ENCRYPTION_KEY | (unset) | 64-hex key required before storing encrypted AI provider keys |
| QA_ALLOW_PUBLIC_AUTH | false | `true` is required for cloud/public deployments; keep `false` for loopback-only hosts |
| QA_COOKIE_SECURE | auto | Set `true` behind HTTPS; Render/proxies sending `X-Forwarded-Proto: https` are detected automatically |
| QA_TRIAL_DURATION_DAYS | 14 | Trial duration in days (1–365); configure the web service and expiry job identically |
| QA_STORAGE_QUOTA_MB | (unset) | Optional positive per-workspace storage quota in MiB |
| QA_DEVELOPER_ERROR_RETENTION_DAYS | 90 | Retention for resolved/ignored error records (1–3650 days) |
| QA_ENVIRONMENT | development | Environment label written to structured logs |
| FRONTEND_URL / CORS_ORIGINS / QA_CORS_ORIGINS | (unset) | Comma-separated allowed origins for a separately served frontend |

## Workspace trials

Each successful signup creates a separate workspace, grants the user the
workspace-scoped `Workspace Owner` role, and starts a 14-day UTC trial. Expiry is
enforced by the backend on every workspace API request. Schedule
`python backend/expire_trials.py` (or `py -3 expire_trials.py` from `backend/`) to
mark expired subscriptions and suspend workspaces in batches. The command must use
the same database configuration as the web service. It is safe to run repeatedly
and does not delete QA data or files.

Existing workspaces are migrated additively to an active `legacy` subscription so
their current access is preserved. Before upgrading, back up
`qa-platform/qa_platform.sqlite3` and the full `qa-platform/storage/` directory.
The local trial-expiry job must be scheduled separately; Render runs it through
the `qa-tracker-trial-expiry` Cron Job.

## Sessions
Sign-in sessions are stored in the database (hashed tokens) and cached in memory
for 12 hours, so users stay signed in across process restarts. Logging out,
deactivating a user, or changing/resetting a password invalidates the matching
sessions. Expired rows are pruned whenever a new session is issued.

## Security Recommendations
- Always use HTTPS in production (reverse proxy + Let's Encrypt)
- Set `QA_COOKIE_SECURE=true` when HTTPS is terminated before the app
- Keep `.env` and `qa-platform/` secure and backed up
- Use strong passwords for accounts
- Don't expose port directly if behind reverse proxy
- Keep the repo updated

## Backup and restore
Run `deploy/backup.sh` from cron (for example every 6 hours). It takes a
consistent SQLite backup (or `pg_dump` when `DATABASE_URL` is set), archives
`qa-platform/storage/`, writes a manifest and SHA-256 checksums, and removes
backups older than `BACKUP_RETENTION_DAYS` (default 30).
Set `BACKUP_RETENTION_DAYS` in the scheduled backup process environment; the script
does not source `.env`.

Before a restore, stop the app and take a fresh backup. Then run
`RESTORE_CONFIRM=yes deploy/restore.sh backups/qa-platform-<timestamp>` and verify
`/health`, `/ready`, sign-in, and a document download. The database is restored from the
backup; the storage archive is extracted over the existing storage folder and
does not remove files absent from that archive. For an exact storage snapshot,
move the current storage folder aside before restoring; the restore command
requires `RESTORE_CONFIRM=yes`.

For a managed PostgreSQL deployment, also enable the provider's point-in-time
recovery and test a restore in a non-production database at least quarterly.
