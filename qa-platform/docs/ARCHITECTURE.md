# QA Platform Architecture and Migration Audit

This document records the current audit and the incremental target architecture.

## Scope and audit baseline

This audit covers `qa-platform/` and the documented sibling REA integration. It does not
inspect or modify the contents of `.env`, the SQLite database, or uploaded/archive files.
The API server uses Python's standard library; AI SDK calls use an optional Node.js
adapter. The UI is a vanilla JavaScript single-page application.

The actual executable entry point is `app.py`. Running it initializes the SQLite schema,
constructs a `ThreadingHTTPServer`, prepares project storage directories, and serves the
static UI and JSON API. REA analysis is launched through a bounded local Node.js process.
The test suite also exercises server startup and HTTP workflows through `app.create_server`.

### Existing dependency flow

```text
app.py
├── ai_provider.py ──> ai_provider.mjs ──> Vercel AI SDK providers
├── ollama_integration.py
│   ├── ai_provider.py
│   ├── requirement_documents.py
│   └── test_case_generator.py
├── application_zip.py
├── requirement_documents.py
└── test_case_generator.py

tests/test_app.py ──> app.py and the Python modules above
ai_provider.test.mjs ──> ai_provider.mjs
static/app.js ──HTTP──> app.py
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
   `qa_platform/core/config.py`; the established root-level entry point remains usable.
3. **Example secret:** `.env.example` contained a literal value instead of a placeholder
   for `QA_ADMIN_PASSWORD`. It has been replaced with setup placeholders. Keep the
   existing `.env` private; do not copy its values into documentation or commits.
4. **Error boundary:** HTTP handlers return some SQLite exception details to clients and
   print tracebacks for unexpected errors. A later API/core migration should preserve
   actionable client errors while routing internal details through redacted,
   structured server logging.
5. **Runtime data and generated files:** `qa_platform.sqlite3`, project uploads, archived
   project folders, Python bytecode, and Node dependencies are present in the workspace.
   They are runtime/user data, not source modules. This refactor does not delete or
   rewrite them. Ignore rules now cover local databases, storage output, caches, virtual
   environments, and Node dependencies; already tracked files remain tracked.
6. **Quality gates:** Python tests exist in `tests/test_app.py`; Node adapter tests exist
   in `ai_provider.test.mjs`. There was no QA Platform CI workflow or formatter/type
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

## Target architecture

Keep the existing local-first Python/SQLite and static-JavaScript application. Migrate
incrementally to this structure; do not introduce a framework or ORM solely to achieve
folder symmetry.

```text
qa-platform/
├── app.py                         # Compatibility entry point
├── qa_platform/
│   ├── api/                       # HTTP routing, request/response contracts
│   ├── core/                      # Configuration, errors, logging, lifecycle
│   ├── models/                    # Domain entities and shared types
│   ├── schemas/                   # Boundary validation and response shapes
│   ├── repositories/              # SQL, transactions, schema migrations
│   ├── services/                  # Requirements, cases, execution, reports
│   └── integrations/              # AI SDKs, ZIP analysis, REA process boundary
├── static/                        # Existing browser application and assets
├── storage/                       # Preserved local project and archive data
├── tests/
│   ├── unit/
│   ├── integration/
│   ├── api/
│   └── test_app.py                # Compatibility/regression coverage during moves
└── docs/
    └── ARCHITECTURE.md
```

Only create a package when a component is actually migrated into it. Keep stable
root-level compatibility imports or wrappers until downstream imports and tests have
moved.

## File-by-file migration mapping

| Current file or data | Target location | Migration notes |
| --- | --- | --- |
| `app.py` | `app.py` plus `qa_platform/api/`, `qa_platform/core/`, `qa_platform/services/` | Keep as the documented launcher while extracting one coherent responsibility at a time. |
| `ai_provider.py` | `qa_platform/integrations/ai/` and `qa_platform/core/config.py` | Provider settings/credential encryption and SDK process client should be separated; retain current module as compatibility facade initially. |
| `ai_provider.mjs` | `qa_platform/integrations/ai/ai_provider.mjs` | Keep server-only key handling, timeouts, and sanitized provider errors. |
| `ollama_integration.py` | `qa_platform/services/requirements_ai.py` and `qa_platform/integrations/ai/` | Separate requirement workflows/output validation from provider-specific transport. |
| `application_zip.py` | `qa_platform/integrations/application_archive.py` | Preserve path validation, size limits, static-only inspection, and atomic staging behavior. |
| `requirement_documents.py` | `qa_platform/services/requirements/document_parser.py` | Keep deterministic extraction and bounded PDF/DOCX handling independent of HTTP. |
| `test_case_generator.py` | `qa_platform/services/test_cases/deterministic_generator.py` | Preserve requirement traceability and deterministic behavior. |
| `static/app.js`, `static/index.html`, `static/style.css` | `static/` (initially unchanged) | Retain the current no-build frontend; split only when it reduces real coupling. |
| `tests/test_app.py` | `tests/api/` and `tests/integration/` over time | Preserve this regression suite while extracting focused service and repository tests. |
| `ai_provider.test.mjs` | `tests/integrations/ai/` or colocated provider tests | Keep deterministic SDK mocks; no network dependency. |
| `qa_platform.sqlite3` | Remains in place | Preserve data and schema compatibility; introduce reversible migrations before schema changes. |
| `storage/projects/`, `storage/archived-projects/` | Remain in place | Preserve uploads and archives; never treat as disposable build output. |
| `requirements.txt`, `package.json`, `package-lock.json` | Remain at project root | Add reproducible Python locking as a separate, validated dependency-management change. |

## Incremental migration plan

1. Establish the current behavior baseline and keep failing tests visible.
2. Centralize configuration and prevent unsafe example credentials from being copied.
3. Extract database connection/schema ownership behind a repository boundary while
   retaining `app.connect`, `app.database`, and `app.initialize` compatibility.
4. Extract API error/response handling and authentication without changing route shapes.
5. Move one service workflow at a time (requirements, generation, execution, reporting).
6. Keep AI and REA subprocesses behind explicit integration interfaces with bounded
   timeouts and sanitized failures; do not retry non-idempotent writes.
7. Split tests to mirror these boundaries and add CI checks before deleting compatibility
   paths or obsolete code.
8. Verify existing database and storage compatibility, full tests, and fresh setup before
   any migration cleanup.

## Changes in this migration slice

- Added `qa_platform/core/config.py` and moved environment-file parsing and database/
  server setting lookup out of the AI provider module.
- Retained `app.py` as the application entry point and preserved the existing SQLite
  default path and HTTP behavior.
- Replaced the `.env.example` admin password value with a placeholder and extended
  `.gitignore` for local runtime output.
- Added a GitHub Actions workflow for Python/Node tests and dependency vulnerability
  audits. Formatting and static type-checking gates remain follow-up work.

The full service/repository/API migration is intentionally not claimed as complete.
