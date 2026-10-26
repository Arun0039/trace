# VS Code AI Master Build Instructions

## 1. Purpose

Build an AI-powered QA Test Management Platform inside VS Code using an AI coding assistant.

The AI coding assistant must actually create the application, not only explain how to build it.

The product solves this QA workflow:

Developer provides:
- Application ZIP
- Requirement document
- API documentation URL or JSON

The platform:
1. Creates a project.
2. Uploads and analyzes the application ZIP.
3. Parses the requirement document.
4. Analyzes API documentation.
5. Uses AI to understand the product.
6. Generates test cases.
7. Maps test cases to requirements.
8. Identifies uncovered requirements.
9. Allows QA review and approval.
10. Allows manual test execution.
11. Captures Actual Result, Status, Screenshot and Comments.
12. Creates bugs from failed test cases.
13. Calculates QA results.
14. Exports reports to Excel.

Core traceability:

Requirement
→ Test Case
→ Execution
→ Evidence
→ Bug
→ Resolution


## 2. Master Instruction for VS Code AI

You are the Lead Software Architect, Senior Full-Stack Developer, AI Engineer, QA Automation Architect, Database Designer and MCP Engineer for this project.

Your responsibility is to BUILD the product described in this document.

Do not only provide recommendations or code examples. Create and modify the actual files.

Do not build the whole application in one response. Build it incrementally by phase.

Before changing code:
1. Inspect the workspace.
2. Read this entire specification.
3. Check existing files and architecture.
4. Reuse working code where appropriate.
5. Do not delete working functionality unnecessarily.

After every major phase:
1. Run tests.
2. Run linting.
3. Run type checks.
4. Build the frontend.
5. Run the backend.
6. Fix errors.
7. Verify the feature.
8. Continue only when the current phase is stable.

If something is ambiguous, make a reasonable MVP assumption, document it briefly and continue.

Do not repeatedly ask for confirmation for normal development decisions.

## 3. Recommended Technology Stack

Frontend:
- React
- TypeScript
- Vite
- Tailwind CSS
- shadcn/ui

Backend:
- Python
- FastAPI
- Pydantic
- SQLAlchemy
- Alembic

Database:
- SQLite for MVP
- PostgreSQL later

Document parsing:
- python-docx
- Markdown parser
- TXT processing

Excel:
- openpyxl

Storage:
- Local filesystem for MVP
- S3-compatible storage later

AI:
- Provider abstraction
- API keys only through environment variables

MCP:
- MCP tool layer calling application services


## 4. Architecture

Use:

Frontend
→ Backend REST API
→ Application Services
→ AI / File / Business Services
→ Repositories
→ Database

Do not put business logic directly inside React components.

Do not put database logic directly inside API route handlers.

MCP must call application services and must not directly manipulate database tables.

Suggested structure:

ai-qa-platform/
├── frontend/
│   └── src/
│       ├── components/
│       ├── pages/
│       ├── layouts/
│       ├── hooks/
│       ├── services/
│       ├── types/
│       └── features/
├── backend/
│   └── app/
│       ├── api/
│       ├── core/
│       ├── models/
│       ├── schemas/
│       ├── repositories/
│       ├── services/
│       ├── ai/
│       ├── parsers/
│       ├── analyzers/
│       ├── reports/
│       ├── mcp/
│       └── main.py
├── database/
├── storage/
│   └── projects/
├── tests/
│   ├── backend/
│   ├── frontend/
│   └── integration/
├── docs/
│   ├── ARCHITECTURE.md
│   ├── API.md
│   ├── DATABASE.md
│   ├── AI.md
│   ├── MCP.md
│   └── DEVELOPMENT.md
├── .env.example
├── .gitignore
└── README.md


## 5. Core Test Case Columns

The main test case table must contain:

- Sno
- Case ID
- Requirement ID
- Scenario
- Description
- Steps
- Expected Result
- Actual Result
- Status
- Screenshot
- Comments

AI primarily generates:
- Case ID
- Requirement ID
- Scenario
- Description
- Steps
- Expected Result

QA manually enters or updates:
- Actual Result
- Status
- Screenshot
- Comments

Statuses:
- Not Executed
- In Progress
- Pass
- Fail
- Blocked
- Skipped


## 6. Users

### QA Engineer
Can:
- Create projects.
- Upload files.
- Analyze requirements.
- Generate test cases.
- Review/edit/approve test cases.
- Execute test cases.
- Upload evidence.
- Create bugs.
- View reports.
- Download Excel.

### QA Lead / Manager
Can:
- View project status.
- Review coverage.
- Review failures.
- Review bugs.
- Download reports.

Developer access can be added later.


# DEVELOPMENT PHASES


## PHASE 0 — Workspace Inspection

Before writing code:

1. Inspect the complete workspace.
2. Check whether frontend exists.
3. Check whether backend exists.
4. Check Node version.
5. Check Python version.
6. Check package managers.
7. Check existing database.
8. Check environment files.
9. Check existing documentation.
10. Determine whether this is an empty or partially built project.

Do not delete an existing application.

If empty, initialize the project.

Create:
- README.md
- .gitignore
- .env.example
- docs/ARCHITECTURE.md

At the end, report what was found.


## PHASE 1 — Project Foundation

Goal: create a working application foundation.

Implement:
1. React frontend.
2. FastAPI backend.
3. SQLite.
4. SQLAlchemy.
5. Alembic.
6. Basic navigation.
7. Project CRUD.
8. Basic dashboard.
9. Health endpoint.

Backend endpoints:

GET /health
POST /projects
GET /projects
GET /projects/{id}
PUT /projects/{id}
DELETE /projects/{id}

Project fields:

- id
- project_key
- name
- description
- version
- environment
- qa_owner
- created_at
- updated_at

Frontend pages:

- Login
- Dashboard
- Projects
- Create Project
- Project Overview

Project tabs:

- Overview
- Requirements
- Test Cases
- Execution
- Bugs
- API
- Files
- Reports

Acceptance criteria:
- User can create a project.
- Project appears in the list.
- Project can be opened.
- Project can be edited.
- Project can be deleted.
- Data persists in SQLite.
- Health endpoint works.
- Frontend communicates with backend.

Do not implement AI, file analysis, MCP or bug tracking yet.


## PHASE 2 — File Management

Implement:

1. Application ZIP upload.
2. Requirement DOCX upload.
3. Requirement MD upload.
4. Requirement TXT upload.
5. API JSON upload.
6. Project-specific storage.
7. File metadata.
8. File validation.
9. File-size limits.
10. Filename sanitization.
11. ZIP path traversal protection.
12. Project isolation.

Endpoints:

POST /projects/{id}/application
POST /projects/{id}/requirements
POST /projects/{id}/api-document

Create uploaded_files:

- id
- project_id
- file_name
- file_type
- file_path
- file_size
- created_at

Storage:

storage/
└── projects/
    └── PROJECT_KEY/
        ├── application/
        ├── requirements/
        ├── api/
        ├── screenshots/
        └── reports/

Never execute uploaded files.

Add security tests for malicious ZIPs and path traversal.


## PHASE 3 — Requirement Intelligence

Support:
- DOCX
- Markdown
- TXT

Extract:
- Requirement ID
- Requirement Title
- Description
- Business Rules
- Acceptance Criteria
- Validation Rules
- Dependencies
- Priority

Create requirements table:

- id
- project_id
- requirement_id
- title
- description
- acceptance_criteria
- business_rules
- validation_rules
- dependencies
- priority
- source_document
- created_at
- updated_at

Use python-docx for DOCX.

Requirements must be editable by QA.

Use structured Pydantic models for extracted requirements.


## PHASE 4 — Application ZIP Analysis

Analyze the uploaded application ZIP.

IMPORTANT:
Uploaded ZIP files are untrusted.
Never execute uploaded application code.

Process:

ZIP
→ Validate
→ Secure extraction
→ File inventory
→ Framework detection
→ Source analysis
→ Application summary

Implement:
- Secure extraction
- File inventory
- File type detection
- Framework/project detection
- Route detection where possible
- Component detection where possible
- Form detection where possible
- Validation detection where possible
- API integration detection
- Configuration detection
- Structured application analysis

Example:

{
  "framework": "React",
  "routes": [],
  "components": [],
  "forms": [],
  "api_integrations": [],
  "validations": [],
  "features": []
}

Do not execute the application.


## PHASE 5 — API Documentation Analysis

Support:
1. OpenAPI JSON.
2. Swagger JSON.
3. Postman JSON where practical.
4. API documentation URL.

Analyze:
- Endpoint
- HTTP method
- Parameters
- Headers
- Authentication
- Request body
- Response body
- Status codes
- Required fields
- Optional fields
- Validation rules
- Error responses

Example:

{
  "endpoint": "/api/users",
  "method": "POST",
  "authentication": "Bearer",
  "required_fields": ["email", "password"],
  "responses": [201, 400, 401, 409]
}

Create api_documents:

- id
- project_id
- source_type
- source_url
- file_path
- parsed_content
- created_at

For URLs implement:
- HTTPS preference
- Timeout
- Response size limit
- Redirect limit
- Content-type validation
- Localhost blocking
- Private IP blocking
- Internal network blocking
- SSRF protection


## PHASE 6 — AI Provider Architecture

Do not tightly couple the application to one AI provider.

Create:

AIProvider

Use environment variables:

AI_PROVIDER=
AI_API_KEY=
AI_MODEL=

Never expose API keys to frontend.

Never commit .env.

Create:
.env.example


## PHASE 7 — AI Skills

Do not use one giant AI prompt.

Create separate services/skills.

### Requirement Analyzer

Input:
Requirement text.

Output:
Structured requirements.

### Application Analyzer

Input:
Application analysis.

Output:
Features, screens, routes, forms, validations, API integrations, roles and permissions.

### API Analyzer

Input:
API documentation.

Output:
Endpoints, methods, parameters, schemas, authentication, errors and validation.

### Product Understanding Builder

Combine:
- Requirements
- Application analysis
- API analysis

Output:

Product
├── Features
├── Requirements
├── Screens
├── User Flows
├── APIs
├── Business Rules
├── Validations
├── Roles
├── Permissions
└── Integrations

### Test Case Generator

Generate:
- Functional
- Positive
- Negative
- Validation
- Boundary
- Permission
- API
- Integration
- Error handling

### Requirement Mapper

Map:

Requirement → Test Case

Output:
- Requirement ID
- Case ID
- Confidence
- Reason

Confidence:
- Exact Match
- High Confidence
- Medium Confidence
- Low Confidence
- Needs Review

### Test Case Reviewer

Detect:
- Missing steps
- Weak expected results
- Missing negative scenarios
- Incorrect mappings
- Duplicate cases
- Unsupported assumptions

### Failure Analyzer

Input:
- Requirement
- Test Case
- Expected Result
- Actual Result
- Screenshot

Output:
- Potential issue
- Likely area
- Possible cause
- Suggested bug title
- Suggested bug description

Always label AI root-cause analysis as a suggestion.

### Bug Report Generator

Generate:
- Title
- Description
- Steps
- Expected Result
- Actual Result
- Severity suggestion
- Priority suggestion

QA must confirm before saving.

### Coverage Analyzer

Identify:
- Covered requirements
- Uncovered requirements
- Unmapped test cases
- Low-confidence mappings

### QA Report Generator

Generate:
- QA summary
- Coverage
- Pass rate
- Failures
- Blocked cases
- Open bugs
- Critical bugs
- Final result


## PHASE 8 — Test Case Generation

Create test_cases:

- id
- project_id
- case_id
- requirement_id
- scenario
- description
- steps
- expected_result
- test_type
- priority
- source
- ai_confidence
- created_at
- updated_at

Generate meaningful test cases.

Example:

Case ID: TC-001
Requirement: REQ-001
Scenario: Login with valid credentials

Description:
Verify that a registered user can successfully log in.

Steps:
1. Open login page.
2. Enter valid username.
3. Enter valid password.
4. Click Login.

Expected Result:
User should be redirected to the dashboard.

Do not generate meaningless duplicates.


## PHASE 9 — Requirement Traceability

This is a CORE feature.

Every approved test case should have a requirement where applicable.

Traceability:

Requirement
→ Test Case
→ Execution
→ Evidence
→ Bug

Example:

REQ-005
→ TC-021
→ FAIL
→ BUG-007

Create a traceability matrix.

Show:
- Requirement ID
- Requirement
- Test Cases
- Execution Status
- Bug Status


## PHASE 10 — Requirement Coverage

Calculate:

Total Requirements
Covered Requirements
Uncovered Requirements
Coverage %

Example:

Total Requirements: 25
Covered: 23
Uncovered: 2
Coverage: 92%

Show:

REQ-001 → TC-001, TC-002 → Covered
REQ-002 → TC-003 → Covered
REQ-003 → No test case → Not Covered

Never silently map uncertain cases.


## PHASE 11 — Duplicate Detection

Detect similar test cases.

Example:

TC-004
≈
TC-019

Similarity: 94%

Show:
Potential Duplicate

QA options:
- Merge
- Keep Both
- Delete One

Never automatically delete a case.


## PHASE 12 — Test Case UI

Build the Test Cases page.

Features:
- Search
- Filter
- Sort
- Pagination
- View
- Edit
- Approve
- Delete
- Bulk Approve
- Generate More
- Duplicate detection
- Requirement mapping
- Coverage indicator

Show columns:

Sno
Case ID
Requirement ID
Scenario
Description
Steps
Expected Result
Actual Result
Status
Screenshot
Comments

Use modal or inline editing.


## PHASE 13 — Test Execution

QA workflow:

Open Test Case
→ Read Steps
→ Test Application
→ Enter Actual Result
→ Upload Screenshot
→ Select Status
→ Add Comments
→ Save

Create test_executions:

- id
- test_case_id
- actual_result
- status
- comments
- executed_by
- executed_at

Preserve execution history.

Do not unnecessarily overwrite previous execution records.


## PHASE 14 — Screenshot / Evidence

Support:
- PNG
- JPG
- JPEG
- WEBP

Create screenshots:

- id
- project_id
- test_case_id
- execution_id
- file_path
- file_name
- uploaded_at

Future:
- Multiple screenshots
- Video
- Console logs
- Network logs
- API request/response


## PHASE 15 — Bug Tracker

A failed test case should have:

Create Bug

Automatically populate:
- Project
- Test Case ID
- Requirement ID
- Scenario
- Steps
- Expected Result
- Actual Result
- Screenshot

Bug fields:

- Bug ID
- Title
- Description
- Requirement ID
- Test Case ID
- Severity
- Priority
- Status
- Assigned To
- Evidence
- Comments
- Created Date
- Resolved Date

Severity:
- Critical
- High
- Medium
- Low

Priority:
- P1
- P2
- P3
- P4

Status:
- Open
- In Progress
- Resolved
- Closed

Create bugs table:

- id
- project_id
- bug_id
- test_case_id
- requirement_id
- title
- description
- severity
- priority
- status
- assigned_to
- created_at
- updated_at
- resolved_at


## PHASE 16 — Bug Traceability

Support:

Bug → Test Case
Bug → Requirement
Test Case → Bug
Requirement → Bug

Example:

REQ-010
→ TC-042
→ BUG-011

Allow navigation between linked records.


## PHASE 17 — Project Dashboard

Show:

- Total Requirements
- Total Test Cases
- Executed
- Not Executed
- Passed
- Failed
- Blocked
- Skipped
- Requirement Coverage
- Pass Percentage
- Open Bugs
- Critical Bugs
- High Bugs

Example:

Requirements: 50
Test Cases: 126
Executed: 110
Passed: 92
Failed: 12
Blocked: 6
Open Bugs: 15
Coverage: 96%
Pass Rate: 84%

Values must come from the database, not hard-coded data.


## PHASE 18 — QA Result

Show final project QA result:

PASSED
or
FAILED

Example:

Requirements: 50
Test Cases: 126
Executed: 120
Passed: 105
Failed: 10
Blocked: 5
Open Bugs: 8
Critical Bugs: 0
High Bugs: 2
Coverage: 96%

Suggested configurable rules:

FAILED if:
- Critical bug exists
OR
- High-priority blocking issue exists
OR
- Required coverage threshold is not met

Otherwise:
PASSED

Show the calculation details to the user.


## PHASE 19 — Excel Export

Use openpyxl.

Create:

### Sheet 1 — Test Cases

Columns:
- Sno
- Case ID
- Requirement ID
- Scenario
- Description
- Steps
- Expected Result
- Actual Result
- Status
- Screenshot
- Comments

### Sheet 2 — Requirement Coverage

Columns:
- Requirement ID
- Requirement
- Mapped Test Cases
- Coverage
- Execution Status

### Sheet 3 — Bug Summary

Columns:
- Bug ID
- Title
- Test Case ID
- Requirement ID
- Severity
- Priority
- Status
- Assigned To

### Sheet 4 — QA Summary

Columns:
- Project
- Version
- Total Requirements
- Total Test Cases
- Executed
- Passed
- Failed
- Blocked
- Coverage
- Pass Rate
- Final QA Result

Use readable formatting, wrapped text and sensible column widths.


## PHASE 20 — UI Design

Create a professional modern SaaS interface.

Requirements:
- Clean layout
- Desktop-first
- Responsive
- Sidebar
- Header
- KPI cards
- Data tables
- Search
- Filters
- Pagination
- Dialogs
- Toast notifications
- Loading states
- Empty states
- Error states
- Confirmation dialogs

Navigation:

Dashboard
Projects

Project:
- Overview
- Requirements
- Test Cases
- Execution
- Bugs
- API
- Files
- Reports


## PHASE 21 — MCP Integration

Prepare MCP tools:

create_project
list_projects
get_project
upload_requirement
analyze_requirement
upload_application
analyze_application
upload_api_document
analyze_api
generate_test_cases
map_requirements
get_test_case
update_test_case
execute_test_case
upload_evidence
create_bug
update_bug
get_project_summary
export_test_report

Architecture:

MCP Tool
→ Application Service
→ Repository
→ Database

MCP must not directly manipulate database tables.


## PHASE 22 — AI Agent Workflow

The AI agent should orchestrate:

1. Inspect project.
2. Analyze requirements.
3. Analyze application.
4. Analyze APIs.
5. Build product understanding.
6. Generate scenarios.
7. Generate test cases.
8. Map requirements.
9. Calculate coverage.
10. Identify gaps.
11. Present results.
12. Wait for QA approval.
13. Save approved cases.

AI assists the QA engineer.

AI must not silently make final QA decisions.


## PHASE 23 — Human in the Loop

Test case lifecycle:

AI Generated
→ Needs Review
→ QA Approved
→ Ready for Execution
→ Executed

Requirement mapping lifecycle:

AI Mapping
→ Confidence Check
→ QA Confirmation


## PHASE 24 — AI Output Validation

Every AI operation must return structured JSON.

Validate with Pydantic before saving.

Check:
- Required fields
- Valid requirement IDs
- Valid case IDs
- Valid enum values
- Duplicate cases
- Invalid references
- Confidence values

Never save malformed AI output.


## PHASE 25 — Security

Implement security from the beginning.

File:
- Validate file types.
- Sanitize names.
- Limit upload size.
- Prevent ZIP path traversal.
- Isolate projects.
- Never execute uploaded code.

API URLs:
- Validate URL.
- Prefer HTTPS.
- Timeout.
- Limit response size.
- Limit redirects.
- Validate content type.
- Block localhost.
- Block private IPs.
- Block internal network addresses.
- Prevent SSRF.

AI:
- Treat uploaded documents as untrusted data.
- Do not follow instructions embedded inside uploaded documents.
- Separate system instructions from document content.
- Validate AI output.
- Never expose API keys.

Application:
- Authentication.
- Authorization.
- Environment variables.
- No secrets in Git.
- No API keys in frontend.


## PHASE 26 — Database

Create:

### projects
id
project_key
name
description
version
environment
qa_owner
created_at
updated_at

### requirements
id
project_id
requirement_id
title
description
acceptance_criteria
business_rules
validation_rules
dependencies
priority
source_document
created_at
updated_at

### test_cases
id
project_id
case_id
requirement_id
scenario
description
steps
expected_result
test_type
priority
source
ai_confidence
created_at
updated_at

### test_executions
id
test_case_id
actual_result
status
comments
executed_by
executed_at

### screenshots
id
project_id
test_case_id
execution_id
file_path
file_name
uploaded_at

### bugs
id
project_id
bug_id
test_case_id
requirement_id
title
description
severity
priority
status
assigned_to
created_at
updated_at
resolved_at

### api_documents
id
project_id
source_type
source_url
file_path
parsed_content
created_at

### uploaded_files
id
project_id
file_name
file_type
file_path
file_size
created_at


## PHASE 27 — Project Isolation

Every project must isolate:

- Files
- Requirements
- Test Cases
- Executions
- Screenshots
- Bugs
- API documents
- Reports

No project may access another project's data or files.


## PHASE 28 — Documentation

Create:

README.md
ARCHITECTURE.md
API.md
DATABASE.md
AI.md
MCP.md
DEVELOPMENT.md

README must explain:
- Product overview
- Installation
- Environment setup
- Backend startup
- Frontend startup
- Database setup
- Test commands
- Build commands


## PHASE 29 — Testing

Backend tests:
- Health
- Project CRUD
- File upload
- Requirement parsing
- ZIP analyzer
- API analyzer
- Database relationships
- Requirement mapping
- AI schema validation
- Test execution
- Bug creation
- Excel export

Frontend tests:
- Project creation
- Project navigation
- Requirement list
- Test case list
- Test case editing
- Test execution
- Bug creation
- Report download

Security tests:
- Malicious ZIP
- Path traversal
- Invalid file type
- Oversized upload
- Cross-project access
- SSRF attempts


# EXACT DEVELOPMENT ORDER

Follow this order:

1. Workspace inspection
2. Project foundation
3. File management
4. Requirement parsing
5. Application ZIP analysis
6. API documentation analysis
7. AI provider abstraction
8. AI skills
9. Test case generation
10. Requirement traceability
11. Requirement coverage
12. Test case UI
13. Test execution
14. Screenshot/evidence
15. Bug tracker
16. Bug traceability
17. Dashboard
18. QA result
19. Excel export
20. UI refinement
21. MCP
22. AI agent workflow
23. Security hardening
24. Production readiness


# PRODUCTION READINESS

After MVP:

- PostgreSQL
- Object storage
- Background workers
- Authentication
- Authorization
- RBAC
- Audit logs
- Structured logging
- Error monitoring
- Rate limiting
- Docker
- CI/CD
- Backups
- Database migrations
- Performance optimization


# AI CODING RULES

1. Do not build everything at once.
2. Do not leave fake functionality pretending to be complete.
3. If credentials are unavailable, create a clean provider abstraction and development adapter.
4. Never hard-code API keys.
5. Never put business logic in UI components.
6. Never silently discard AI output.
7. Never automatically approve AI-generated cases.
8. Never automatically delete duplicate cases.
9. Never execute uploaded application code.
10. Never allow cross-project data access.
11. Do not rewrite unrelated working modules.
12. Use meaningful names.
13. Use typed models.
14. Handle errors.
15. Add logging.
16. Add tests for important logic.
17. Run tests after major changes.
18. Fix errors before continuing.


# RESPONSE FORMAT FOR THE AI CODING ASSISTANT

At the end of each task, report:

1. What was implemented.
2. Files created.
3. Files modified.
4. Database changes.
5. API endpoints.
6. UI pages/components.
7. Tests executed.
8. Test results.
9. Known issues.
10. Next phase.


# MASTER START COMMAND

Read this entire specification before modifying the project.

Do not implement everything at once.

First inspect the workspace.

Then implement PHASE 0 and PHASE 1 only.

Perform:

1. Inspect workspace.
2. Confirm existing technology.
3. Create project structure.
4. Initialize frontend.
5. Initialize backend.
6. Configure SQLite.
7. Configure SQLAlchemy.
8. Configure Alembic.
9. Create Project model.
10. Create Project CRUD APIs.
11. Create project management UI.
12. Create basic dashboard.
13. Create navigation.
14. Create README.
15. Create .env.example.
16. Add tests.
17. Run tests.
18. Run frontend build.
19. Fix errors.

Do not start file upload, AI analysis, test generation, MCP or bug tracking yet.

At the end report:
- Architecture created.
- Files created.
- Files modified.
- APIs implemented.
- Database tables.
- UI pages.
- Tests passed.
- Remaining Phase 1 work.

Then stop.


# PHASE 2 COMMAND

Continue with PHASE 2.

Implement file management only.

Build:
- Application ZIP upload.
- DOCX upload.
- MD upload.
- TXT upload.
- API JSON upload.
- Project storage.
- File metadata.
- File validation.
- File size limits.
- Filename sanitization.
- ZIP path traversal protection.
- Project isolation.

Do not implement AI analysis yet.

Run tests and fix all errors.


# PHASE 3 COMMAND

Continue with PHASE 3.

Implement requirement intelligence.

Build:
- DOCX parser.
- Markdown parser.
- TXT parser.
- Requirement extraction.
- Requirement database records.
- Requirement APIs.
- Requirement UI.
- Requirement review/edit.

Extract:
- Requirement ID.
- Title.
- Description.
- Business Rules.
- Acceptance Criteria.
- Validation Rules.
- Dependencies.
- Priority.

Do not start test generation yet.


# PHASE 4 COMMAND

Continue with PHASE 4.

Implement application ZIP analysis.

Build:
- Secure extraction.
- File inventory.
- File type detection.
- Framework detection.
- Source analysis.
- Route detection where possible.
- Component detection where possible.
- Form detection where possible.
- Validation detection where possible.
- API integration detection.
- Application summary.

Never execute uploaded code.

Add security tests.


# PHASE 5 COMMAND

Continue with PHASE 5.

Implement API documentation analysis.

Support:
- OpenAPI JSON.
- Swagger JSON.
- Postman JSON where practical.
- API documentation URL.

Implement:
- Endpoint catalog.
- Request schemas.
- Response schemas.
- Authentication analysis.
- Validation analysis.
- Error response analysis.

For URLs implement SSRF protection.

Add tests.


# PHASE 6 COMMAND

Continue with PHASE 6.

Implement the AI provider abstraction.

Build:
- AIProvider interface.
- Provider configuration.
- Environment configuration.
- Structured output.
- Pydantic validation.
- Timeout handling.
- Retry handling where appropriate.
- Logging.
- Error handling.

Never expose AI credentials to the frontend.


# PHASE 7 COMMAND

Continue with PHASE 7.

Implement these AI services independently:

1. Requirement Analyzer.
2. Application Analyzer.
3. API Analyzer.
4. Product Understanding Builder.
5. Test Case Generator.
6. Requirement Mapper.
7. Test Case Reviewer.
8. Failure Analyzer.
9. Bug Report Generator.
10. Coverage Analyzer.
11. QA Report Generator.

Do not use one giant prompt.

Validate every AI response before saving.


# PHASE 8 COMMAND

Continue with PHASE 8.

Implement test case generation.

Input:
- Requirements.
- Application Analysis.
- API Analysis.
- Product Understanding.

Generate:
- Functional.
- Positive.
- Negative.
- Validation.
- Boundary.
- Permission.
- API.
- Integration.
- Error handling.

Implement duplicate detection, confidence scoring and requirement mapping.

Do not automatically approve cases.


# PHASE 9 COMMAND

Continue with PHASE 9.

Build the complete Test Case UI.

Features:
- Search.
- Filter.
- Sort.
- Pagination.
- View.
- Edit.
- Approve.
- Delete.
- Bulk approve.
- Generate more.
- Duplicate detection.
- Requirement mapping.
- Coverage indicator.


# PHASE 10 COMMAND

Continue with PHASE 10.

Build test execution.

QA must be able to:
1. Open test case.
2. View steps.
3. View expected result.
4. Enter actual result.
5. Upload screenshot.
6. Select status.
7. Add comments.
8. Save execution.

Preserve execution history.


# PHASE 11 COMMAND

Continue with PHASE 11.

Build bug tracker.

Add:
- Create bug.
- Edit bug.
- Bug list.
- Search.
- Filter.
- Severity.
- Priority.
- Status.
- Assignment.

Failed test cases must have Create Bug.

Automatically populate test information.

Allow QA to edit before saving.


# PHASE 12 COMMAND

Continue with PHASE 12.

Implement traceability:

Requirement → Test Case
Test Case → Execution
Test Case → Bug
Requirement → Bug

Create traceability UI.

Add tests.


# PHASE 13 COMMAND

Continue with PHASE 13.

Build dashboard.

Show:
- Requirements.
- Test Cases.
- Executed.
- Passed.
- Failed.
- Blocked.
- Skipped.
- Coverage.
- Pass Rate.
- Open Bugs.
- Critical Bugs.
- High Bugs.

All values must come from database calculations.


# PHASE 14 COMMAND

Continue with PHASE 14.

Implement configurable QA result rules.

Example:

FAILED if:
- Critical bug exists.
- High-priority blocking issue exists.
- Coverage is below threshold.

Otherwise:
PASSED.

Show the calculation details.


# PHASE 15 COMMAND

Continue with PHASE 15.

Implement Excel export using openpyxl.

Sheets:
1. Test Cases.
2. Requirement Coverage.
3. Bug Summary.
4. QA Summary.

Format the workbook professionally.


# PHASE 16 COMMAND

Continue with PHASE 16.

Refine the UI.

Improve:
- Sidebar.
- Header.
- Dashboard.
- Tables.
- Filters.
- Forms.
- Dialogs.
- Toasts.
- Loading states.
- Empty states.
- Error states.
- Responsive layout.

Do not change business logic unnecessarily.


# PHASE 17 COMMAND

Continue with PHASE 17.

Implement MCP architecture.

Tools:
- create_project
- list_projects
- get_project
- upload_requirement
- analyze_requirement
- upload_application
- analyze_application
- upload_api_document
- analyze_api
- generate_test_cases
- map_requirements
- get_test_case
- update_test_case
- execute_test_case
- upload_evidence
- create_bug
- update_bug
- get_project_summary
- export_test_report

MCP tools must call application services.


# PHASE 18 COMMAND

Continue with PHASE 18.

Implement AI agent orchestration:

1. Inspect project.
2. Analyze requirements.
3. Analyze application.
4. Analyze APIs.
5. Build product understanding.
6. Generate test cases.
7. Map requirements.
8. Calculate coverage.
9. Identify gaps.
10. Present results.
11. Wait for QA approval.


# PHASE 19 COMMAND

Continue with PHASE 19.

Perform security hardening.

Review:
- Authentication.
- Authorization.
- File uploads.
- ZIP extraction.
- Path traversal.
- SSRF.
- Project isolation.
- API keys.
- Environment variables.
- CORS.
- Rate limiting.
- Input validation.
- AI prompt injection.
- AI output validation.
- Error leakage.

Add security tests.


# PHASE 20 COMMAND

Continue with PHASE 20.

Prepare production readiness:

- PostgreSQL.
- Object storage.
- Background workers.
- Authentication.
- Authorization.
- RBAC.
- Audit logs.
- Monitoring.
- Structured logging.
- Docker.
- CI/CD.
- Backup.
- Error tracking.
- Performance review.

Do not break the existing MVP.


# ERROR FIXING COMMAND

When I provide an error, use this instruction:

"The current implementation has the following error:

[ERROR]

Find the root cause.

Do not rewrite unrelated modules.

Make the smallest appropriate fix.

Run the relevant tests again.

Confirm whether the issue is fixed."


# FINAL MVP ACCEPTANCE CRITERIA

The MVP is complete when a QA engineer can do all of this without manually maintaining Excel:

1. Create Project.
2. Upload application ZIP.
3. Upload Requirement.docx.
4. Upload API JSON.
5. Click Analyze.
6. AI extracts requirements.
7. AI analyzes application.
8. AI analyzes APIs.
9. AI builds product understanding.
10. AI generates test cases.
11. AI maps test cases to requirements.
12. AI identifies coverage gaps.
13. QA reviews test cases.
14. QA approves test cases.
15. QA executes test cases.
16. QA enters Actual Result.
17. QA uploads screenshots.
18. QA selects status.
19. QA adds comments.
20. QA creates bugs from failures.
21. Dashboard calculates QA result.
22. Requirement coverage is displayed.
23. Bug summary is displayed.
24. Final QA result is displayed.
25. Excel report downloads successfully.


# CORE PRODUCT PRINCIPLE

AI GENERATES AND ANALYZES.

QA VALIDATES AND APPROVES.

THE PLATFORM MAINTAINS TRACEABILITY.

Complete traceability:

Requirement
→ Test Case
→ Execution
→ Evidence
→ Bug
→ Resolution
