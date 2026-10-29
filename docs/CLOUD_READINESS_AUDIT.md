# Cloud Readiness Audit

Date: 2026-10-29
Scope: TRACE QA Platform (`backend/app.py`, `frontend/`, deployment and CI assets)

## Verdict

**Suitable only for a controlled, single-instance pilot after the deployment
checklist is satisfied. Not approved for unrestricted production or multi-instance
scaling.**

The application now has server-side authorization, tenant-scoped role assignment,
persistent hashed sessions, login rate limiting, hardened response headers, safe
document download paths, parameterized SQL, and automated security checks. The
remaining blockers include deployment-topology risks and incomplete production
assurance: local file storage and rate limits are instance-local, several
uniqueness constraints are still global rather than tenant-scoped, containers run
as root, and an actual restore drill and production deployment have not been
verified. The Render Blueprint now specifies a paid single instance with a
persistent storage disk; its capacity and backups still require operational
monitoring.

Recommended pilot conditions:

- Managed PostgreSQL with TLS (`DATABASE_URL`) instead of SQLite.
- HTTPS termination in front of the app and `QA_COOKIE_SECURE=true`.
- Cloudflare Pages and Render custom domains under the same registrable domain;
  set an exact `QA_CORS_ORIGINS` entry for the Pages origin.
- Persistent storage mounted at `/app/qa-platform/storage` (or a planned
  object-storage migration).
- One application instance until shared storage/rate limiting is introduced.
- Scheduled `deploy/backup.sh` plus a tested restore.

## Phase results

| Phase | Status | Evidence / notes |
|---|---|---|
| 1. Inventory | Complete | Backend is a stdlib `ThreadingHTTPServer`; frontend is vanilla JS; SQLite is the default, PostgreSQL is supported; Cloudflare Pages build wiring is now included. |
| 2. Configuration and secrets | Partial | `.env.example` documents runtime settings; Pages gets a public API origin at build time and Render uses an exact CORS origin. Gitleaks is configured in CI but no completed workflow was verified. |
| 3. Database and migrations | Partial | PostgreSQL connections use a per-process `psycopg-pool` pool capped at 10. Nullable foreign-key cycles are deferred/restored by the SQLite-to-PostgreSQL migrator. A disposable migration preserved all 42 table row counts and a project ID. Most schema changes are still additive startup migrations; only `product_owner` is versioned. Global unique constraints remain. |
| 4. Application security | Partial | Security controls are implemented and high-severity Bandit is clean; the medium-severity findings remain, and no external penetration test was run. |
| 5. Auth and tenant isolation | Implemented; regression-tested | Tenant-scoped role lookup on user create/update; persisted sessions; logout, deactivation, and password changes invalidate sessions. No independent penetration test was performed. |
| 6. Storage | Complete for local storage | Downloads resolve inside the project storage directory and reject symlinks/traversal. Object storage is not implemented. |
| 7. Regression tests | Complete for this pass; CI pending | All 132 Python tests pass in the Python 3.11 production image against disposable PostgreSQL 16, including PostgreSQL integration coverage; 9 Node tests pass. |
| 8. Performance | Partial | Added tenant indexes and a 10-connection-per-process PostgreSQL pool. SQLite remains single-writer; keep one app instance until storage and rate limits are shared. |
| 9. CI/CD | Implemented; workflow run pending | CI runs Python/Node tests and dependency/security checks, builds the Pages artifact, and builds the container. A PostgreSQL service and integration test are configured. This review did not inspect a completed GitHub Actions run. |
| 10. Backup and DR | Partial | Backup/restore scripts and runbook added. An actual restore drill must be performed against the pilot environment. |
| 11. Cloudflare Pages | Implemented; deployment pending | Static build injects the configured Render API origin, writes route rewrites, and uses credentialed cross-origin requests. Custom domains, exact CORS configuration, and browser login must be verified after deployment. |

## Security changes in this pass

- Login, admin login, and signup attempts are rate limited by IP and account
  (`429` with `Retry-After`); successful sign-in clears the account counter.
- Session cookies add `Secure` when `QA_COOKIE_SECURE=true` or the proxy reports
  HTTPS. Responses include CSP, `X-Frame-Options: DENY`, `Referrer-Policy:
  no-referrer`, `Permissions-Policy`, and `nosniff`.
- Sessions are persisted as SHA-256 token hashes in `auth_sessions`, cached in
  memory for 12 hours, and survive process restarts. Expired rows are pruned.
- Database exceptions are logged server-side; clients receive generic messages.
- Requirement document downloads must resolve inside the project's storage folder.
- DOCX XML is parsed with `defusedxml`; size and ZIP-entry limits remain enforced.
- `current_user` role selection for user create/update is restricted to the
  actor's tenant or a global system role.
- Public sign-in is no longer implicitly enabled just because the server binds
  `0.0.0.0`; `QA_ALLOW_PUBLIC_AUTH=true` is now an explicit cloud decision.
- Cross-origin frontend clients use the configured HTTPS API origin and send
  credentialed requests. The Render Blueprint requires an exact Pages origin in
  `QA_CORS_ORIGINS`; custom domains under the same site are required to preserve
  the current `SameSite=Strict` cookie policy.
- The Pages build generates security headers, API configuration, and SPA route
  rewrites. The Render Blueprint uses native readiness checks and a persistent
  disk; a duplicate health-check Cron Job and its helper script were removed.
- Product usage's daily/monthly active-workspace counts now honor the selected
  date range and reject reversed date intervals.
- Real PostgreSQL verification found and fixed two startup/request compatibility
  defects: a table referenced `users` before that table existed, and numeric
  `GROUP_CONCAT` values were not cast for PostgreSQL `STRING_AGG`.

## Verification evidence

This pass executed the following checks:

- Backend suite: 132 tests passed in the Python 3.11 production image against
  disposable PostgreSQL 16 with `QA_TEST_POSTGRES_URL` configured.
- An earlier full-suite attempt reported one DOCX-import/document-list test
  failure; the test passed in isolation and in the subsequent full-suite run.
  The failure was not reproduced.
- Cloudflare Pages build: passed with a placeholder HTTPS API origin; generated
  `config.js`, `_headers`, and `_redirects` were inspected.
- JavaScript syntax checks passed for the frontend entry points and build script.
- Node adapter suite: 9 tests passed.
- `npm audit --audit-level=high`: zero vulnerabilities.
- Bandit high-severity scan: zero high findings; 57 medium findings remain.

The following results are retained from an earlier audit and were not reproduced
in this pass:

```text
backend: python -m unittest discover -s tests -v
         128 tests, OK (2 PostgreSQL-only tests skipped without QA_TEST_POSTGRES_URL)

backend: npm test
         9 tests, 9 pass

frontend: node --check frontend/app.js
          node --check frontend/product-owner.js
          node --check frontend/developer.js
          all syntax checks pass

backend: npm audit --audit-level=high
         found 0 vulnerabilities

backend: pip-audit -r requirements.txt   (Python 3.13 audit venv)
         No known vulnerabilities found

backend: bandit -r . -x ./tests -lll
         exit 0 (no high-severity findings)

backend: bandit -r . -x ./tests -ll
         57 medium findings: 55 B608 (dynamic SQL construction),
         2 B104 (0.0.0.0 container bind); medium findings are not a clean scan

container: docker build -t trace-qa-readiness:local .
           build passed; image was not vulnerability-scanned

PostgreSQL: two dedicated integration tests against PostgreSQL 16
            schema initialization, transactional signup/trial, admin login,
            Product Owner APIs and SQLite-to-PostgreSQL migration passed

Migration: migrate_sqlite_to_postgres.migrate against PostgreSQL 16
           row counts verified for 42 tables; project ID and record preserved
```

In this pass, `pip-audit -r backend/requirements.txt` failed under the selected
Python 3.14 environment because its installed `charset-normalizer` crashes while
initializing. The earlier clean audit used Python 3.13 and predates the new
`psycopg-pool` dependency. The GitHub Actions workflow, gitleaks, live
Render/PostgreSQL connectivity, Cloudflare browser login, container image scan,
and restore drill were not executed. The PostgreSQL 16 tests were run against a
disposable local database, not the live Render service.

## Residual risks and follow-ups

### P1 — before public production

1. **Instance-local file storage.** The Blueprint mounts a 1 GB persistent disk
   for uploads and archives, but it is not shared across instances. Monitor
   capacity, keep one instance, configure off-host backups, and test a restore;
   move to object storage before horizontal scaling.
2. **Global unique names.** `teams.name`, `roles.name`, and `users.email` are
   globally unique. Two tenants cannot use the same team/role name. Introduce
   tenant-scoped unique indexes with a reversible migration (approval required).
3. **CSP allows inline script/style.** The frontend emits inline `style` and
   `onclick` handlers and imports Google Fonts. Moving handlers/styles to files and
   self-hosting fonts would allow removing `'unsafe-inline'`.
4. **In-memory rate limits.** Limits reset on restart and are not shared across
   instances. Use a shared store (Redis/PostgreSQL) before horizontal scaling.
5. **Single-process SQLite.** Use PostgreSQL for any multi-user pilot.
6. **Container hardening.** The Docker image runs as root and uses the mutable
   `python:3.11-slim` tag. Add a non-root runtime user without breaking mounted
   storage permissions, pin the base image, and scan the final image before
   public production.

### P2 — hardening and operations

1. Validate a full restore drill and document recovery time/point objectives.
   The restore script overlays archived files on the destination storage folder;
   files created after the backup remain unless the current folder is moved aside.
2. Add a reversible migration runner for all schema changes, not only the
   `product_owner` feature.
3. Add structured audit-log retention and export.
4. Replace the one-off PostgreSQL migration script's dynamic SQL with a generated
   table allowlist helper (currently reviewed and fixed-input only).
5. Add dependency update automation and container image scanning in CI.
6. Define backup encryption, off-host retention, and recovery-point/recovery-time
   objectives; perform and record a restore drill.

## Deployment checklist

- [ ] `DATABASE_URL` points at managed PostgreSQL with `sslmode=require`.
- [ ] Cloudflare Pages `QA_API_BASE_URL` points at the HTTPS Render API origin.
- [ ] Pages and API custom domains share a registrable domain, and Render's
      `QA_CORS_ORIGINS` is the exact Pages origin.
- [ ] `QA_ADMIN_PASSWORD` and `QA_AI_ENCRYPTION_KEY` are unique and stored as secrets.
- [ ] `QA_ALLOW_PUBLIC_AUTH=true` and `QA_COOKIE_SECURE=true` for the cloud service.
- [ ] HTTPS terminates in front of the app; direct port access is blocked.
- [ ] The persistent disk is mounted; backups run on a schedule and are copied off-host.
- [ ] `/health` and `/ready` are green and a browser sign-in plus document
      upload/download smoke test passes.
- [ ] Restore has been rehearsed in a non-production environment.
