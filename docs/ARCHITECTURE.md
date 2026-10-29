# QA Platform Architecture and Migration Audit

This document records the current audit and the incremental target architecture.

## Scope and audit baseline

This audit covers `frontend/`, `backend/`, and `docs/`. It does not inspect or modify
the contents of `qa-platform/.env`, the SQLite database, or uploaded/archive files.
The API server uses Python's standard library; AI SDK calls use an optional Node.js
adapter. The UI is a vanilla JavaScript single-page application.

The executable entry point is `backend/app.py`. Running it initializes the SQLite schema,
constructs a `ThreadingHTTPServer`, prepares project storage directories, and serves the
static UI from `frontend/` and JSON API. App Analysis is marked Coming soon and has no
runtime dependency on the removed REA repository. The backend tests also exercise server
startup and HTTP workflows through `app.create_server`.

### Existing dependency flow

```text
backend/app.py
├── backend/ai_provider.py ──> backend/ai_provider.mjs ──> Vercel AI SDK providers
├── backend/ollama_integration.py
│   ├── backend/ai_provider.py
│   ├── backend/requirement_documents.py
│   └── backend/test_case_generator.py
├── backend/application_zip.py
└── backend/qa_platform/core/

backend/tests/test_app.py ──> backend/app.py and the Python modules above
backend/ai_provider.test.mjs ──> backend/ai_provider.mjs
frontend/app.js ──HTTP──> backend/app.py
```

No circular import is evident in the inspected Python modules. `app.py` is the central
coupling point: it owns schema creation, SQLite connection/transaction helpers, shared
domain logic, file handling, authentication, API dispatch, response handling, background
analysis scheduling, and process startup. Its roughly 3,160 lines combine those
responsibilities in one module. The separate parser, archive analyzer, deterministic
generator, and AI adapter are useful existing boundaries and should be retained during
migration.

Several names in the requested inventory differ from the actual files:

| Requested name | Existing file |
| --- | --- |
| `ai_provider.ms` | `ai_provider.mjs` |
| `ai_provider_test.mjs` | `ai_provider.test.mjs` |
| `application.zip.py` | `application_zip.py` |
| `requirements_document.py` | `requirement_documents.py` |

There is no separate `application.zip.py` or `requirements_document.py` to migrate.

## Findings and risks

1. **Oversized application module:** SQL statements, request dispatch, filesystem
   operations, and business workflows are interleaved in `app.py`. This increases the
   cost and regression risk of changes. Request validation and authorization are also
   implemented alongside route handlers rather than as a dedicated API boundary.
2. **Configuration ownership:** `.env` parsing previously lived in `ai_provider.py`,
   while database and server settings were read separately in `app.py`. The initial
   refactoring slice moves shared paths and environment loading into
   `backend/qa_platform/core/config.py`; the backend entry point remains usable.
3. **Example secret:** `.env.example` contained a literal value instead of a placeholder
   for `QA_ADMIN_PASSWORD`. It has been replaced with setup placeholders. Keep the
   existing `.env` private; do not copy its values into documentation or commits.
4. **Error boundary:** HTTP handlers return some SQLite exception details to clients and
   print tracebacks for unexpected errors. A later API/core migration should preserve
   actionable client errors while routing internal details through redacted,
   structured server logging.
5. **Runtime data and generated files:** `qa-platform/qa_platform.sqlite3`, project uploads,
   archived project folders, Python bytecode, and Node dependencies are present in the workspace.
   They are runtime/user data, not source modules. This refactor does not delete or
   rewrite them. Ignore rules now cover local databases, storage output, caches, virtual
   environments, and Node dependencies; already tracked files remain tracked.
6. **Quality gates:** Python tests exist in `backend/tests/test_app.py`; Node adapter tests
   exist in `backend/ai_provider.test.mjs`. There was no QA Platform CI workflow or formatter/type
   checker configured in this app at audit time. The Python baseline was 60 tests with
   one failure: a ZIP unit test expected a client-side `fetch('/api/...')` path to appear
   as a server route, contrary to the HTTP regression test and the analyzer's separate
   API-integration inventory. The assertion now checks the integration inventory instead;
   application behavior is unchanged. The Node tests could not be run in this environment
   because `npm` was unavailable on the selected process PATH.
7. **Dependency reproducibility:** `requirements.txt` expresses lower bounds rather
   than a Python lock file. Node dependencies are constrained by `package-lock.json`,
   and `npm ci` is documented. Python dependency locking and security-update policy
   remain follow-up work.

## Reusable form services

Custom fields are tenant-scoped definitions associated with a supported form key
(`projects`, `requirements`, `test-cases`, or `bugs`). `custom_fields` stores field
metadata, `custom_field_options` stores dropdown choices, and
`custom_field_values` stores values by module and record ID. The API validates active
field values when records are created or edited; record schemas do not gain per-field
columns. Field-management endpoints require Global Admin access, while form
definitions and record values are served through a shared endpoint. New modules can
extend the supported module map without changing the value-storage schema.

`form_attachments` is a shared attachment store keyed by project, module, and record.
The API accepts bounded raw file uploads, computes a content digest to reject duplicates,
and serves separate inline-view and download routes. Project, requirement, test-case,
and defect forms use the same upload/list/remove flow. Existing execution screenshot
evidence remains on its image-specific table and endpoint for backward compatibility.

## User management, RBAC, and tenant context

The existing schema is upgraded in place to add a default `tenants` row, tenant
memberships, normalized permission and role-permission tables, and tenant identifiers
for projects, project-owned records, teams, custom fields, and audit events. Existing
users receive an active membership in the default workspace using their current role;
existing project data is assigned to that workspace. A user can have multiple
memberships, and `X-Tenant-ID` is accepted only when the authenticated user has an
active membership in that tenant.

The configured `QA_ADMIN_PASSWORD` session remains the local bootstrap Global Admin.
The `Global Admin` role is also available for explicitly provisioned user memberships.
Public signup creates a dedicated workspace and grants its registering user the
tenant-scoped `Workspace Owner` role; the role is not a platform administrator.
Administrative account creation defaults to `User`, and signup cannot claim the
reserved `global-admin@local.qa` address. A one-time migration promotes an
existing account with that address to the `Global Admin` role in the default workspace
only. This account receives full permissions within its active tenant membership, but
does not become a system-wide administrator. Its project, user, team, role, custom
field, and audit operations remain tenant-scoped. Removing a member who has other
workspace memberships removes only the current workspace membership; the shared user
account and other memberships remain. The configured bootstrap Global Admin remains
the system-wide administrator and retains system-only archive and provider settings.
Administrative endpoints enforce role permissions and tenant context in the API; the
UI uses the same permission keys but is not the security boundary.

## Workspace storage and developer monitoring

The workspace Storage page is served by the existing vanilla JavaScript app at
`/workspace/storage`. The backend scopes project files and supported records to the
authenticated tenant before generating inventory identifiers, downloads, or deletion
operations. Files on disk and binary attachment/evidence payloads are measured in
exact bytes. For project, requirement, test-case, execution, defect, and analysis
rows, the estimate sums the character lengths of selected text fields plus a fixed
64-byte row allowance. It deliberately excludes encoding expansion, indexes,
database pages, and engine-specific overhead. The UI distinguishes exact data from
estimates; totals and quota include both.

File deletion and deletion of eligible project/type groups are tenant checked and
audited. Record-group deletion uses the database transaction and removes linked form
data and execution evidence along with its parent rows. Projects and analysis-job
rows are protected in the Storage page: the project-management delete action retains
its existing archive behavior, and running/operational analysis state is not exposed
as a destructive storage target. `workspace_storage_snapshots` caches summary values
per tenant; snapshots are invalidated after deletion and refreshed by recalculation.
Its additive startup migration removes stale snapshots when the estimation fields are
added.

Developer monitoring is a separate static application at `/developer`, with access
provided by the existing sign-in flow. The API checks `developer.view`, `.manage`,
or `.delete` for every protected operation and scopes error data to the developer's
tenant unless the authenticated user is the system Global Admin. Workspace business
records are not made available through these endpoints. Sanitized error details are
stored in the independent `developer_errors` table; resolved and ignored records are
pruned using `QA_DEVELOPER_ERROR_RETENTION_DAYS` (default 90 days). The public,
rate-limited reporting endpoint accepts only bounded frontend/API diagnostics and
redacts secrets, emails, IPs, and local user paths. HTTP and exception logs are
structured and omit request query strings.

The dashboard's API failure rates are derived from a bounded process-local buffer
of the latest 20,000 authenticated non-developer API responses, split by status and
tenant. These counts reset on restart and are explicitly not durable logs. Database,
filesystem, and analysis-job checks are exposed only through the developer health
API. `QA_STORAGE_QUOTA_MB` configures the optional workspace quota; `QA_ENVIRONMENT`
labels structured logs and error metadata.

Project requests are scoped to the authenticated tenant before record handlers run.
Project-owned requirements, test cases, defects, attachments, and related records
inherit the tenant of their project, and the write path synchronizes their
`tenant_id`. Tenant-scoped role and status changes update only the membership, while
system-wide account changes may also update the legacy user role/status columns.
User-management actions record the actor, action, target, tenant, timestamp, and
before/after values in `audit_events`. Account deletion removes memberships and team
memberships but preserves QA history, team ownership is nulled, and audit actor
references are retained with `ON DELETE SET NULL`.

The existing local installation retains its default workspace and does not add tenant
switching. New signup provisions a separate tenant. A `Global Admin` role assigned
through a tenant membership is intentionally scoped to that tenant; only the configured
bootstrap administrator receives system-wide access.

## Trial lifecycle and data preservation

`tenant_subscriptions` stores a workspace's plan, lifecycle status, trial start, and
exclusive UTC expiry. The startup migration is additive: it adds `owner_user_id`,
suspension metadata, and the subscription table, then seeds any pre-existing tenant
without a subscription as an active `legacy` plan. This preserves current local
projects and does not assign records to a different tenant.

Workspace 001 (tenant ID 1) is the permanent no-payment workspace. Startup repairs
an earlier trial/suspension state to active `legacy`, and access checks and the
expiry worker exclude it; newly created signup workspaces receive the trial.

Signup creates the tenant, owner account, owner membership, trial subscription, and
audit event in one transaction. Tenant-scoped API requests pass through the shared
request authorizer, which denies suspended workspaces and expired trials even when
the scheduled worker has not run. Auth status, login/logout, and self-service profile
routes remain available after suspension. `backend/expire_trials.py` runs idempotent
conditional updates in bounded batches, records a suspension audit event, and never
deletes tenant data or files. Render schedules this command separately from the public
health check. A system Global Admin can reactivate an expired trial through the
authorized workspace subscription route; the operation starts a new configured-length
trial and is audited. `QA_TRIAL_DURATION_DAYS` defaults to 14 days.

Before deploying, back up the local SQLite database and `qa-platform/storage/`, or
take a PostgreSQL snapshot. The schema is additive and can remain after application
rollback. If the expiry worker has already suspended tenants, restore the backup or
use the authorized reactivation route before reverting. Billing and paid-plan
checkout are not part of this implementation.

## Product Owner Operations

The separate `/product-owner` static application uses the existing bootstrap
administrator's HttpOnly session. All `/api/product-owner/*` routes independently
require the system Global Admin; a workspace membership with a Global Admin role is
not sufficient. The UI offers an overview, tenant directory/detail, trial and plan
operations, storage inventory/cleanup, usage analytics, error/health monitoring, and
administrative audit history.

Migration `0001_product_owner_dashboard` assigns immutable unique public UUIDs to
users, tenants, and workspaces while preserving existing numeric IDs and foreign
keys. Since `tenants` is the established workspace entity, tenant and workspace
public IDs are separate UUID columns on that row rather than a new workspace table.
The migration adds a plan catalog, coarse usage events, storage snapshots, and
Product Owner audit rows. Plan catalog configuration, subscription lifecycle, and
workspace access status remain separate concerns; billing is not implemented.

Storage inventory reuses the established tenant-scoped scanner and safe deletion
service. Deletion requires inventoried identifiers, a tenant-specific confirmation,
a reason, and audit history. Database record sizes remain estimates; filesystem and
binary storage are measured separately. Usage events retain only normalized API
routes/statuses and login/signup events; request counts in the existing developer
monitor are still process-local. `QA_TRIAL_DURATION_DAYS` configures the trial
duration, defaulting to 14 days.

## Target architecture

Keep the existing local-first Python/SQLite and static-JavaScript application. The
frontend and backend are now separate folders; continue any deeper code migration
incrementally without introducing a framework or ORM solely to achieve folder symmetry.

```text
project-root/
├── frontend/                      # Static browser application and dev server
├── backend/                       # Python API, domain modules, dependencies, and tests
├── docs/
└── qa-platform/                   # Preserved .env, database, uploads, and archives
```

Only create a package when a component is actually migrated into it. Keep stable
root-level compatibility imports or wrappers until downstream imports and tests have
moved.

## File-by-file migration mapping

| Current file or data | Target location | Migration notes |
| --- | --- | --- |
| `backend/app.py` | `backend/app.py` plus `backend/qa_platform/` | Keep as the documented entry point while extracting one coherent responsibility at a time. |
| `backend/ai_provider.py` | `backend/ai_provider.py` and `backend/qa_platform/core/config.py` | Provider settings, credential encryption, and SDK process client remain in backend modules. |
| `backend/ai_provider.mjs` | `backend/ai_provider.mjs` | Keep server-only key handling, timeouts, and sanitized provider errors. |
| `backend/ollama_integration.py` | `backend/ollama_integration.py` | Keep requirement workflows and provider integration in existing modules. |
| `backend/application_zip.py` | `backend/application_zip.py` | Preserve path validation, size limits, static-only inspection, and atomic staging behavior. |
| `backend/requirement_documents.py` | `backend/requirement_documents.py` | Keep deterministic extraction and bounded PDF/DOCX handling independent of HTTP. |
| `backend/test_case_generator.py` | `backend/test_case_generator.py` | Preserve requirement traceability and deterministic behavior. |
| `frontend/app.js`, `frontend/index.html`, `frontend/style.css` | `frontend/` | Retain the no-build vanilla JavaScript frontend and configurable API base URL. |
| `backend/tests/` | `backend/tests/` | Preserve the regression suite and existing migration/database tests. |
| `backend/ai_provider.test.mjs` | `backend/ai_provider.test.mjs` | Keep deterministic SDK mocks; no network dependency. |
| `qa-platform/qa_platform.sqlite3` | Remains in place | Preserve data and schema compatibility; introduce reversible migrations before schema changes. |
| `qa-platform/storage/projects/`, `qa-platform/storage/archived-projects/` | Remain in place | Preserve uploads and archives; never treat as disposable build output. |
| `backend/requirements.txt`, `backend/package.json`, `backend/package-lock.json` | `backend/` | Keep Python and optional Node dependencies with the backend. |

## Incremental migration plan

1. Establish the current behavior baseline and keep failing tests visible.
2. Centralize configuration and prevent unsafe example credentials from being copied.
3. Extract database connection/schema ownership behind a repository boundary while
   retaining `app.connect`, `app.database`, and `app.initialize` compatibility.
4. Extract API error/response handling and authentication without changing route shapes.
5. Move one service workflow at a time (requirements, generation, execution, reporting).
6. Keep AI subprocesses behind explicit integration interfaces with bounded timeouts
   and sanitized failures; do not retry non-idempotent writes.
7. Split tests to mirror these boundaries and add CI checks before deleting compatibility
   paths or obsolete code.
8. Verify existing database and storage compatibility, full tests, and fresh setup before
   any migration cleanup.

## Changes in this migration slice

- Added `qa_platform/core/config.py` and moved environment-file parsing and database/
  server setting lookup out of the AI provider module.
- Separated the static UI and backend source while preserving the existing SQLite
  database/storage paths and same-origin application behavior.
- Added opt-in CORS for an independently served frontend and kept the default same-origin
  configuration restricted.
- Made AI provider settings and encrypted credentials workspace-scoped, with any signed-in
  workspace member able to configure Google Gemini, OpenAI, Anthropic Claude, or an
  OpenAI-compatible endpoint once a workspace administrator enables the module. Ollama is
  restricted to the Default Workspace.
- Replaced the `.env.example` admin password value with a placeholder and extended
  `.gitignore` for local runtime output.
- Added a GitHub Actions workflow for Python/Node tests and dependency vulnerability
  audits. Formatting and static type-checking gates remain follow-up work.

The full service/repository/API migration is intentionally not claimed as complete.
