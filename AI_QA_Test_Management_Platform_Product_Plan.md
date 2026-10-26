# AI-Powered QA Test Case & Requirement Mapping Platform

## 1. Product Overview

### Product Name
**AI QA Test Management Platform**

### Objective

Build a web-based QA product that reduces the manual effort required to:

- Analyze a developer-provided application ZIP file.
- Analyze requirement documents.
- Analyze Product API documentation from a URL or JSON file.
- Automatically identify application functionality and requirements.
- Generate structured QA test cases using AI.
- Map every test case back to the relevant requirement.
- Allow QA engineers to manually execute the generated test cases.
- Capture Actual Result, Status, Screenshots, and Comments.
- Track bugs from failed test cases.
- Generate project-level QA results.
- Download the final test execution report as Excel.

The platform should support **multiple projects**, where each project has its own requirements, application build, API documentation, test cases, execution results, and bug records.

---

# 2. Core Problem

Today the QA workflow is largely manual:

1. Developer shares an application ZIP file.
2. QA receives the requirement document.
3. QA understands the application and requirements.
4. QA manually prepares test cases in Excel.
5. QA manually maps test cases to requirements.
6. QA executes the application.
7. QA enters actual results and pass/fail status.
8. QA captures screenshots manually.
9. Failed cases are converted into bug reports.
10. Final QA results are prepared manually.

### Main Problems

- Test case creation takes significant time.
- Requirement-to-test-case mapping can be missed.
- Requirements may not have complete test coverage.
- API functionality may be overlooked.
- Test cases are stored in separate Excel files.
- Execution history is difficult to maintain.
- Screenshots and evidence are difficult to organize.
- Bug tracking is disconnected from test execution.
- Project-level QA status requires manual calculation.
- Reusing the same workflow for multiple projects is difficult.

---

# 3. Product Goal

Create a centralized QA workspace where a QA engineer can:

```text
Create Project
      ↓
Upload Application ZIP
      ↓
Upload Requirement Document
      ↓
Add API Documentation
      ↓
AI Analysis
      ↓
Requirement Extraction
      ↓
Application Analysis
      ↓
API Analysis
      ↓
AI Test Case Generation
      ↓
Requirement Mapping
      ↓
QA Review / Edit
      ↓
Execute Test Cases
      ↓
Capture Evidence
      ↓
Raise / Track Bugs
      ↓
Generate QA Result
      ↓
Download Excel Report
```

---

# 4. Primary Users

## QA Engineer

Main user of the product.

Responsibilities:

- Create projects.
- Upload builds.
- Upload requirements.
- Add API documentation.
- Review AI-generated test cases.
- Execute test cases.
- Enter actual results.
- Upload screenshots.
- Mark Pass/Fail/Blocked.
- Create bugs.
- Generate reports.

## QA Lead / Manager

Responsibilities:

- View project QA status.
- Review requirement coverage.
- Review failed cases.
- Review open bugs.
- Download reports.
- Monitor project progress.

## Developer

Optional future role:

- View failed test cases.
- View linked bugs.
- Review bug evidence.
- View QA feedback.

---

# 5. Project Structure

Each project should be isolated.

Example:

```text
Project
│
├── Project Details
│
├── Application
│   └── application.zip
│
├── Requirements
│   ├── requirement.docx
│   ├── requirement.md
│   └── requirement.txt
│
├── API Documentation
│   ├── API URL
│   └── API JSON
│
├── AI Analysis
│
├── Requirements
│
├── Test Cases
│
├── Test Execution
│
├── Screenshots / Evidence
│
├── Bug Tracker
│
└── Reports
    └── Excel
```

---

# 6. Project Creation

## Step 1: Create Project

The user should be able to create a new project.

### Fields

| Field | Type | Required |
|---|---|---|
| Project Name | Text | Yes |
| Project Key | Text | Yes |
| Description | Textarea | No |
| Application Version | Text | No |
| Environment | Text | No |
| QA Owner | User | Yes |
| Created Date | Auto | Yes |

Example:

```text
Project Name: Customer Portal
Project Key: CP001
Version: 2.4.0
Environment: QA
QA Owner: Arun
```

---

# 7. Application Upload

## Step 2: Upload Developer ZIP File

The developer provides the application ZIP file.

### Supported Input

```text
application.zip
```

### System Responsibilities

The system should:

1. Validate ZIP file.
2. Store the original file.
3. Extract the ZIP into a project-specific workspace.
4. Identify supported file types.
5. Detect project structure.
6. Identify configuration files.
7. Identify UI-related files.
8. Identify API-related files.
9. Identify routes/endpoints where technically possible.
10. Prepare extracted content for AI analysis.

### Important Security Requirement

The ZIP file must be treated as **untrusted input**.

The system must:

- Prevent path traversal.
- Restrict executable processing.
- Limit upload size.
- Scan files where possible.
- Use an isolated extraction directory.
- Never execute uploaded application code automatically.

---

# 8. Requirement Document Upload

## Step 3: Upload Requirement

Supported formats:

- DOCX
- Markdown
- TXT

Example:

```text
Requirement.docx
Requirement.md
Requirement.txt
```

### System Responsibilities

1. Store original document.
2. Extract text.
3. Preserve headings where possible.
4. Detect requirement IDs.
5. Identify functional requirements.
6. Identify business rules.
7. Identify validation rules.
8. Identify acceptance criteria.
9. Identify dependencies.
10. Create structured requirement records.

---

# 9. Requirement Extraction

AI should convert the requirement document into structured data.

### Requirement Model

| Field | Description |
|---|---|
| Requirement ID | Unique requirement identifier |
| Requirement Title | Requirement name |
| Requirement Description | Detailed requirement |
| Business Rule | Applicable rule |
| Acceptance Criteria | Expected behavior |
| Priority | High/Medium/Low |
| Source | Document/page/section |
| Status | Active/Reviewed |

Example:

```text
REQ-001
Title: User Login

Description:
User should be able to log in using valid credentials.

Acceptance Criteria:
- Valid username should be accepted.
- Valid password should be accepted.
- Invalid password should display an error.
- Empty fields should show validation.
```

---

# 10. API Documentation Input

## Step 4: Add Product API Documentation

The user should be able to provide API documentation in two ways.

### Option A: API URL

Example:

```text
https://example.com/api-docs
```

### Option B: API JSON

Example:

```text
openapi.json
swagger.json
api.json
```

### Supported API Documentation

Prefer support for:

- OpenAPI / Swagger
- REST API JSON
- Postman collection JSON
- API specification JSON

Future support:

- GraphQL schema
- HAR files
- Postman collections
- API documentation websites

---

# 11. API Analysis

AI should analyze API documentation and identify:

- Endpoint
- HTTP method
- Parameters
- Headers
- Authentication
- Request body
- Response body
- Response codes
- Validation rules
- Required fields
- Optional fields
- Error conditions
- Business rules

Example:

```text
POST /api/users

Required:
email
password

Expected:
201 Created

Possible errors:
400 Bad Request
401 Unauthorized
409 Conflict
```

---

# 12. AI Analysis Engine

The AI analysis engine is the core component.

It should analyze three sources:

```text
Requirement Document
        +
Application ZIP
        +
API Documentation
        ↓
    AI Analysis
        ↓
Unified Product Understanding
```

The AI should not generate test cases immediately from only one source.

It should first create an internal product understanding model.

---

# 13. Product Understanding Model

Create an intermediate representation such as:

```text
Product
│
├── Features
├── Requirements
├── User Flows
├── Screens
├── API Endpoints
├── Business Rules
├── Validations
├── Roles
├── Permissions
├── Dependencies
└── Integrations
```

This model becomes the foundation for test case generation.

---

# 14. Test Case Generation

## Step 5: Generate Test Cases

AI should generate test cases using:

1. Requirements.
2. Application structure.
3. API documentation.
4. Business rules.
5. Acceptance criteria.
6. Validation rules.
7. Positive scenarios.
8. Negative scenarios.
9. Boundary conditions.
10. Permission scenarios.
11. Error scenarios.
12. API scenarios.

---

# 15. Test Case Types

The system should support:

### Functional

```text
Valid login
Invalid login
Logout
Create user
Edit user
Delete user
```

### Negative

```text
Invalid email
Invalid password
Missing required field
Invalid data format
Unauthorized access
```

### Boundary

```text
Minimum length
Maximum length
Zero value
Maximum allowed value
Empty input
```

### Validation

```text
Required field
Email format
Date format
Number format
Duplicate value
```

### Role / Permission

```text
Admin
Agent
Manager
Read-only user
Unauthorized user
```

### API

```text
Valid request
Invalid request
Missing parameter
Invalid authentication
Invalid payload
Unexpected response
```

### Integration

```text
External service unavailable
API timeout
Invalid third-party response
Data synchronization failure
```

---

# 16. Test Case Output

The main test case table must contain:

| Column | Source |
|---|---|
| Sno | System |
| Case ID | AI/System |
| Requirement ID | Requirement |
| Scenario | AI |
| Description | AI |
| Steps | AI / Manual |
| Expected Result | AI |
| Actual Result | QA |
| Status | QA |
| Screenshot | QA |
| Comments | QA |

### Important Rule

The following fields are primarily generated from requirements and analysis:

```text
Case ID
Requirement ID
Scenario
Description
Steps
Expected Result
```

The QA engineer manually enters or updates:

```text
Actual Result
Status
Screenshot
Comments
```

---

# 17. Test Case Example

| Field | Example |
|---|---|
| Case ID | TC-001 |
| Requirement ID | REQ-001 |
| Scenario | Login with valid credentials |
| Description | Verify that a registered user can log in |
| Steps | 1. Open login page 2. Enter valid username 3. Enter valid password 4. Click Login |
| Expected Result | User should successfully log in and reach dashboard |
| Actual Result | |
| Status | Not Executed |
| Screenshot | |
| Comments | |

---

# 18. Requirement Coverage

The system must automatically calculate requirement coverage.

Example:

```text
Total Requirements: 25
Requirements Covered: 23
Requirements Not Covered: 2

Coverage: 92%
```

The system should identify:

```text
REQ-001 → TC-001, TC-002
REQ-002 → TC-003
REQ-003 → No Test Case
```

This is one of the most important features.

---

# 19. AI Requirement Mapping

The AI should map test cases to requirements.

Mapping confidence can be stored:

```text
Exact Match
High Confidence
Medium Confidence
Low Confidence
Needs Review
```

Example:

```text
TC-001
REQ-001
Confidence: 98%
Reason:
Test case validates the login acceptance criteria.
```

If the AI cannot confidently map a case, it should flag the mapping for QA review instead of silently assigning the wrong requirement.

---

# 20. Requirement-to-Test Coverage Matrix

Provide a dedicated view:

| Requirement | Test Cases | Coverage | Status |
|---|---|---|---|
| REQ-001 | TC-001, TC-002 | Covered | Pass |
| REQ-002 | TC-003 | Covered | Fail |
| REQ-003 | - | Not Covered | Attention |

This allows the QA engineer to identify missing test coverage.

---

# 21. Test Execution

The QA engineer should be able to execute cases directly inside the web application.

### Status Values

```text
Not Executed
In Progress
Pass
Fail
Blocked
Skipped
```

### Execution Flow

```text
Open Test Case
      ↓
Read Steps
      ↓
Test Application
      ↓
Enter Actual Result
      ↓
Upload Screenshot
      ↓
Select Status
      ↓
Add Comments
      ↓
Save Execution
```

---

# 22. Screenshot / Evidence Management

For each test case:

```text
Upload Screenshot
```

The system should support:

- PNG
- JPG
- JPEG
- WEBP

Optional future support:

- Multiple screenshots
- Video evidence
- Browser console logs
- Network logs
- API request/response evidence

Screenshots should be stored against:

```text
Project
Test Case
Execution
```

---

# 23. Bug Tracker

Add a lightweight bug tracker inside each project.

## Bug Creation

A failed test case should have:

```text
Create Bug
```

The system should automatically copy:

- Project
- Test Case ID
- Requirement ID
- Scenario
- Steps
- Expected Result
- Actual Result
- Screenshot

### Bug Fields

| Field | Description |
|---|---|
| Bug ID | System generated |
| Title | Bug title |
| Description | Bug description |
| Requirement ID | Linked requirement |
| Test Case ID | Linked test case |
| Severity | Critical/High/Medium/Low |
| Priority | P1/P2/P3/P4 |
| Status | Open/In Progress/Resolved/Closed |
| Assigned To | Developer |
| Evidence | Screenshot/files |
| Comments | Notes |
| Created Date | System |
| Resolved Date | System |

---

# 24. Bug-Test Case Relationship

Maintain traceability:

```text
Requirement
    ↓
Test Case
    ↓
Execution
    ↓
Failure
    ↓
Bug
```

Example:

```text
REQ-005
   ↓
TC-021
   ↓
FAIL
   ↓
BUG-007
```

This should allow the user to navigate between all related records.

---

# 25. Project Dashboard

Each project should have a dashboard.

### KPIs

```text
Total Requirements
Total Test Cases
Executed
Not Executed
Passed
Failed
Blocked
Skipped
Open Bugs
Closed Bugs
Requirement Coverage
Pass Percentage
```

Example:

```text
Requirements          50
Test Cases            126
Executed              110
Passed                 92
Failed                 12
Blocked                 6
Open Bugs              15
Coverage               96%
Pass Rate             84%
```

---

# 26. QA Result

The system should provide a final QA summary.

Example:

```text
Project: Customer Portal
Version: 2.4.0

Requirement Coverage: 96%

Total Test Cases: 126
Executed: 120
Passed: 105
Failed: 10
Blocked: 5

Open Bugs: 8
Critical Bugs: 0
High Bugs: 2

QA Result: FAILED
```

### Suggested QA Decision Rules

Example:

```text
FAILED if:
- Critical bug exists
OR
- High-priority failure exists
OR
- Required coverage threshold is not met
```

These rules should be configurable per project.

---

# 27. Excel Export

The user should be able to download the final test report.

### Export Formats

Phase 1:

```text
.xlsx
```

Future:

```text
.csv
.pdf
```

### Excel Sheet 1: Test Cases

Columns:

```text
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
```

### Excel Sheet 2: Requirement Coverage

```text
Requirement ID
Requirement
Mapped Test Cases
Coverage
Execution Status
```

### Excel Sheet 3: Bug Summary

```text
Bug ID
Title
Test Case ID
Requirement ID
Severity
Priority
Status
Assigned To
```

### Excel Sheet 4: QA Summary

```text
Project
Version
Total Requirements
Total Test Cases
Executed
Passed
Failed
Blocked
Coverage
Pass Rate
Final QA Result
```

---

# 28. Local Database

The initial product should use a local database.

Recommended Phase 1:

```text
SQLite
```

Reason:

- Easy local setup.
- No separate database server required.
- Good for MVP.
- Easy backup.
- Simple development.
- Can later migrate to PostgreSQL.

---

# 29. Suggested Database Structure

## projects

```text
id
project_key
name
description
version
environment
qa_owner
created_at
updated_at
```

## requirements

```text
id
project_id
requirement_id
title
description
acceptance_criteria
priority
source_document
created_at
```

## test_cases

```text
id
project_id
case_id
requirement_id
scenario
description
steps
expected_result
source
ai_confidence
created_at
updated_at
```

## test_executions

```text
id
test_case_id
actual_result
status
comments
executed_by
executed_at
```

## screenshots

```text
id
project_id
test_case_id
execution_id
file_path
file_name
uploaded_at
```

## bugs

```text
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
```

## api_documents

```text
id
project_id
source_type
source_url
file_path
parsed_content
created_at
```

## uploaded_files

```text
id
project_id
file_name
file_type
file_path
file_size
created_at
```

---

# 30. Recommended Architecture

Use a modular architecture.

```text
Frontend
   ↓
Backend API
   ↓
Application Services
   ↓
AI Analysis Service
   ↓
Database
   ↓
File Storage
```

Suggested components:

```text
Web UI
Backend API
AI Orchestrator
Document Parser
ZIP Analyzer
API Analyzer
Test Case Generator
Requirement Mapper
Test Execution Service
Bug Service
Report Generator
Database
File Storage
```

---

# 31. Suggested Technology Stack

The exact stack can be decided during implementation.

### Frontend

Recommended:

```text
React
TypeScript
```

UI:

```text
Tailwind CSS
```

Optional component library:

```text
shadcn/ui
```

### Backend

Recommended:

```text
Python
FastAPI
```

Alternative:

```text
Node.js
TypeScript
```

### Database

MVP:

```text
SQLite
```

Production:

```text
PostgreSQL
```

### File Storage

Local MVP:

```text
Local filesystem
```

Production:

```text
S3-compatible object storage
```

### Excel

Python:

```text
openpyxl
```

### Document Parsing

DOCX:

```text
python-docx
```

Markdown:

```text
Markdown parser
```

Text:

```text
Native text processing
```

---

# 32. AI Layer

The AI layer should not be tightly coupled to one model.

Create an AI provider abstraction.

```text
AIProvider
│
├── OpenAI
├── Other LLM Provider
└── Local Model
```

This makes the product flexible.

---

# 33. AI Skills

The AI system should have dedicated skills/modules.

## Skill 1: Requirement Analyzer

Input:

```text
DOCX / MD / TXT
```

Output:

```text
Structured Requirements
```

Responsibilities:

- Identify requirements.
- Extract requirement IDs.
- Extract acceptance criteria.
- Identify business rules.
- Identify validation rules.
- Identify dependencies.
- Detect duplicate requirements.
- Detect ambiguous requirements.

---

## Skill 2: Application Analyzer

Input:

```text
Application ZIP
```

Output:

```text
Application Structure
```

Responsibilities:

- Identify project type.
- Identify pages/screens.
- Identify components.
- Identify routes.
- Identify forms.
- Identify validations.
- Identify API integrations.
- Identify configuration.
- Identify testable functionality.

Important:

The analyzer should inspect source code/configuration where possible but must not execute untrusted application code by default.

---

## Skill 3: API Analyzer

Input:

```text
API URL / API JSON
```

Output:

```text
API Catalog
```

Responsibilities:

- Identify endpoints.
- Identify methods.
- Identify parameters.
- Identify request schemas.
- Identify response schemas.
- Identify authentication.
- Identify error responses.
- Identify validation rules.

---

## Skill 4: Test Case Generator

Input:

```text
Requirements
+
Application Analysis
+
API Analysis
```

Output:

```text
Test Cases
```

Responsibilities:

- Generate positive cases.
- Generate negative cases.
- Generate boundary cases.
- Generate validation cases.
- Generate permission cases.
- Generate API cases.
- Generate integration cases.

---

## Skill 5: Requirement Mapper

Input:

```text
Requirements
+
Test Cases
```

Output:

```text
Requirement ↔ Test Case Mapping
```

Responsibilities:

- Map cases to requirements.
- Calculate confidence.
- Detect unmapped cases.
- Detect uncovered requirements.
- Flag uncertain mappings for QA review.

---

## Skill 6: Test Review Assistant

The QA engineer should be able to ask:

```text
Review this test case.
```

AI can identify:

- Missing steps.
- Weak expected result.
- Missing negative scenario.
- Missing validation.
- Incorrect requirement mapping.
- Duplicate test cases.

---

## Skill 7: Failure Analysis

When the QA marks a test case as Failed, AI can analyze:

```text
Requirement
+
Test Case
+
Expected Result
+
Actual Result
+
Screenshot
```

and suggest:

```text
Potential Issue
Likely Area
Possible Cause
Suggested Bug Title
Suggested Bug Description
```

The AI should clearly label this as a suggestion rather than a confirmed root cause.

---

## Skill 8: Bug Draft Generator

From a failed test:

```text
Generate Bug
```

AI prepares:

```text
Title
Description
Steps
Expected Result
Actual Result
Severity Suggestion
Priority Suggestion
```

QA remains responsible for final confirmation.

---

# 34. REA MCP Integration

The product should be designed to support an MCP-based AI workflow.

Recommended approach:

```text
AI Agent
   ↓
MCP Tool Layer
   ↓
QA Platform Services
```

Potential MCP tools:

```text
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
```

---

# 35. MCP Tool Example

Example conceptual tool:

```text
generate_test_cases(
    project_id,
    requirement_ids?,
    include_api_tests=true,
    include_negative_tests=true,
    include_boundary_tests=true
)
```

Expected result:

```text
{
  "test_cases": [
    {
      "case_id": "TC-001",
      "requirement_id": "REQ-001",
      "scenario": "Valid Login",
      "description": "...",
      "steps": ["...", "..."],
      "expected_result": "..."
    }
  ]
}
```

---

# 36. AI Agent Workflow

The AI agent should operate as an orchestrator.

```text
1. Inspect project
2. Inspect requirement document
3. Inspect application
4. Inspect API documentation
5. Build product understanding
6. Identify requirements
7. Generate test scenarios
8. Generate test cases
9. Map requirements
10. Detect coverage gaps
11. Present results
12. Wait for QA approval
13. Save approved cases
```

Do not automatically overwrite QA-edited test cases.

---

# 37. Human-in-the-Loop

AI should assist the QA engineer, not replace the QA engineer.

Recommended states:

```text
AI Generated
     ↓
Needs Review
     ↓
QA Approved
     ↓
Ready for Execution
     ↓
Executed
```

For requirement mappings:

```text
AI Mapping
     ↓
Confidence Check
     ↓
QA Confirmation
```

---

# 38. Application UI Pages

## Page 1: Login

```text
Email
Password
Login
```

## Page 2: Dashboard

```text
Projects
Total Test Cases
Open Bugs
Overall Pass Rate
```

## Page 3: Projects

```text
Project List
Create Project
Search
Filter
```

## Page 4: Project Overview

Tabs:

```text
Overview
Requirements
Test Cases
Execution
Bugs
API
Files
Reports
```

## Page 5: Upload Center

```text
Application ZIP
Requirement Document
API URL
API JSON
```

## Page 6: AI Analysis

Show progress:

```text
Reading Requirements
Analyzing Application
Analyzing APIs
Building Product Model
Generating Test Cases
Mapping Requirements
Checking Coverage
```

## Page 7: Test Cases

Features:

```text
Search
Filter
Sort
Edit
Approve
Bulk Approve
Generate More
Duplicate
Delete
```

## Page 8: Test Execution

Provide:

```text
Steps
Expected Result
Actual Result
Screenshot
Status
Comments
```

## Page 9: Bugs

```text
Bug List
Create Bug
Filter
Severity
Priority
Status
```

## Page 10: Reports

```text
QA Summary
Requirement Coverage
Test Execution
Bug Summary
Download Excel
```

---

# 39. MVP Scope

Do not build every feature in the first version.

## Phase 1 — Foundation

Build:

- Project creation.
- Local database.
- File upload.
- Project isolation.
- Basic dashboard.
- Requirement upload.

## Phase 2 — Requirement Intelligence

Build:

- DOCX parser.
- MD parser.
- TXT parser.
- Requirement extraction.
- Requirement review/edit.

## Phase 3 — Application Analysis

Build:

- ZIP upload.
- Secure extraction.
- File inventory.
- Source analysis.
- Basic application structure detection.

## Phase 4 — API Intelligence

Build:

- API JSON upload.
- OpenAPI parsing.
- API endpoint catalog.
- API validation analysis.

## Phase 5 — AI Test Generation

Build:

- Test scenario generation.
- Test case generation.
- Positive tests.
- Negative tests.
- Validation tests.
- API tests.
- Requirement mapping.

## Phase 6 — QA Execution

Build:

- Test execution.
- Actual result.
- Status.
- Screenshot.
- Comments.
- Execution history.

## Phase 7 — Bug Tracker

Build:

- Create bug from failed case.
- Bug fields.
- Bug status.
- Test case linkage.
- Requirement linkage.

## Phase 8 — Reporting

Build:

- Dashboard.
- Coverage report.
- Pass/fail report.
- Bug summary.
- Excel export.

---

# 40. Phase 1 Detailed Action Items

## Action 1 — Initialize Repository

Create:

```text
qa-platform/
├── frontend/
├── backend/
├── ai/
├── database/
├── storage/
├── docs/
├── tests/
└── README.md
```

## Action 2 — Create Git Workflow

Branches:

```text
main
develop
feature/*
bugfix/*
```

## Action 3 — Setup Backend

Implement:

```text
GET /health
```

Then:

```text
POST /projects
GET /projects
GET /projects/{id}
PUT /projects/{id}
DELETE /projects/{id}
```

## Action 4 — Setup Database

Create migrations for:

```text
projects
requirements
test_cases
test_executions
screenshots
bugs
api_documents
uploaded_files
```

## Action 5 — Build File Upload

Implement:

```text
POST /projects/{id}/application
POST /projects/{id}/requirements
POST /projects/{id}/api-document
```

## Action 6 — Build Requirement Parser

Implement:

```text
DOCX → Text
MD → Text
TXT → Text
```

Then send structured content to AI.

## Action 7 — Build ZIP Analyzer

Implement:

```text
ZIP
 ↓
Secure Extraction
 ↓
File Inventory
 ↓
Supported Source Analysis
 ↓
Application Summary
```

## Action 8 — Build API Analyzer

Implement:

```text
OpenAPI JSON
 ↓
Parser
 ↓
Endpoint Catalog
 ↓
Validation Rules
```

## Action 9 — Build AI Requirement Analyzer

Input:

```text
Requirement Text
```

Output:

```text
Requirement JSON
```

## Action 10 — Build AI Test Generator

Input:

```text
Requirement JSON
Application Analysis
API Analysis
```

Output:

```text
Test Case JSON
```

## Action 11 — Build Mapping Engine

Input:

```text
Requirements
Test Cases
```

Output:

```text
Requirement ID
Case ID
Confidence
Reason
```

## Action 12 — Build Test Case UI

Allow:

```text
View
Edit
Approve
Delete
Filter
Search
```

## Action 13 — Build Execution UI

Allow:

```text
Actual Result
Status
Screenshot
Comments
Save
```

## Action 14 — Build Bug Tracker

Allow:

```text
Create Bug
Edit Bug
Link Test Case
Link Requirement
Change Status
```

## Action 15 — Build Dashboard

Calculate:

```text
Total Test Cases
Passed
Failed
Blocked
Not Executed
Coverage
Open Bugs
Pass Rate
```

## Action 16 — Build Excel Export

Generate:

```text
Test Cases
Requirement Coverage
Bug Summary
QA Summary
```

---

# 41. AI Prompt Architecture

Do not use one giant prompt for everything.

Use separate prompts.

```text
Requirement Analyzer Prompt
Application Analyzer Prompt
API Analyzer Prompt
Product Understanding Prompt
Test Case Generator Prompt
Requirement Mapping Prompt
Test Review Prompt
Bug Generator Prompt
Failure Analysis Prompt
```

Each AI response should follow a strict JSON schema.

---

# 42. Structured AI Output

Example:

```json
{
  "case_id": "TC-001",
  "requirement_id": "REQ-001",
  "scenario": "Valid Login",
  "description": "Verify that a registered user can log in.",
  "steps": [
    "Open login page",
    "Enter valid username",
    "Enter valid password",
    "Click Login"
  ],
  "expected_result": "User should be redirected to the dashboard.",
  "test_type": "Functional",
  "priority": "High",
  "confidence": 0.98
}
```

Use schema validation before saving AI output to the database.

---

# 43. AI Quality Controls

AI-generated test cases should pass validation before appearing as final cases.

Check:

- Case ID exists.
- Requirement ID exists.
- Scenario is not empty.
- Description is not empty.
- Steps exist.
- Expected result exists.
- Requirement mapping is valid.
- Duplicate cases are detected.
- Unsupported assumptions are flagged.

---

# 44. Duplicate Detection

AI may generate duplicate scenarios.

The system should identify:

```text
TC-004 ≈ TC-019
```

and show:

```text
Potential Duplicate
Similarity: 94%
```

QA can:

```text
Merge
Keep Both
Delete One
```

---

# 45. Traceability Matrix

Create a full traceability view:

```text
Requirement
      ↓
Test Scenario
      ↓
Test Case
      ↓
Execution
      ↓
Bug
      ↓
Resolution
```

This will become one of the product's key differentiators.

---

# 46. Security Requirements

Because users upload application source code and documents:

- Validate file types.
- Limit upload size.
- Prevent ZIP path traversal.
- Sanitize filenames.
- Isolate project files.
- Prevent uploaded scripts from executing.
- Restrict API URL fetching.
- Protect against SSRF when fetching API documentation URLs.
- Apply request timeouts.
- Limit response size.
- Do not expose files across projects.
- Protect AI prompts from untrusted document instructions.
- Keep project data separated.
- Add authentication before production use.

---

# 47. API URL Security

If users provide an API documentation URL, do not blindly fetch any URL.

Implement:

```text
URL validation
Domain policy
HTTPS preference
Timeout
Redirect limits
Response-size limits
Content-type validation
Private IP blocking
Localhost blocking
Internal network blocking
```

This is important to prevent SSRF vulnerabilities.

---

# 48. Local MVP Storage

For the first version:

```text
SQLite
+
Local file storage
```

Example:

```text
storage/
└── projects/
    ├── CP001/
    │   ├── application/
    │   ├── requirements/
    │   ├── api/
    │   ├── screenshots/
    │   └── reports/
    │
    └── CP002/
```

---

# 49. Future Production Architecture

After MVP validation:

```text
React
   ↓
API Gateway
   ↓
FastAPI Services
   ↓
PostgreSQL
   ↓
Object Storage
   ↓
AI Service
   ↓
Queue / Worker
```

Use background jobs for:

- Large ZIP analysis.
- Document parsing.
- AI analysis.
- Test generation.
- Excel generation.

---

# 50. Recommended Development Order

Follow this exact order.

```text
1. Repository
2. Backend
3. Database
4. Project Management
5. File Upload
6. Requirement Parser
7. Requirement UI
8. ZIP Analyzer
9. API Analyzer
10. AI Requirement Analysis
11. AI Test Generation
12. Requirement Mapping
13. Test Case UI
14. Test Execution
15. Screenshot Evidence
16. Bug Tracker
17. Dashboard
18. Excel Export
19. AI Review Assistant
20. MCP Integration
21. Authentication
22. Production Security
```

---

# 51. Definition of Done — MVP

The MVP is complete when a QA engineer can perform this workflow without Excel:

```text
Create Project
      ↓
Upload ZIP
      ↓
Upload Requirement.docx
      ↓
Upload API JSON
      ↓
Click Analyze
      ↓
AI extracts requirements
      ↓
AI analyzes application
      ↓
AI analyzes APIs
      ↓
AI generates test cases
      ↓
AI maps test cases to requirements
      ↓
QA reviews cases
      ↓
QA executes cases
      ↓
QA enters actual result
      ↓
QA uploads screenshot
      ↓
QA marks Pass/Fail
      ↓
QA creates bugs for failures
      ↓
Dashboard calculates QA result
      ↓
Download Excel
```

---

# 52. Suggested Product Differentiators

The product should not position itself only as an AI test-case generator.

The stronger product proposition is:

> **AI-powered requirement-to-test traceability and QA execution platform.**

Key differentiators:

1. Requirement → Test Case automatic mapping.
2. Application ZIP → functional understanding.
3. API documentation → API test coverage.
4. Requirement coverage detection.
5. Human approval of AI-generated cases.
6. Test execution and evidence in the same platform.
7. Failed test → bug creation.
8. Full Requirement → Test → Execution → Bug traceability.
9. Project-level QA decision.
10. Excel-compatible reporting.
11. MCP-ready QA automation layer.

---

# 53. Suggested Product Name Ideas

Possible names:

```text
QATrace AI
TestTrace AI
ReqTest AI
QAFlow AI
TraceQA
TestPilot QA
Req2Test
QAForge
TestMatrix AI
QualityFlow
```

---

# 54. Initial Success Metrics

Measure the product using:

### Test Case Creation Time

Before:

```text
2–4 hours
```

Target:

```text
10–30 minutes including review
```

### Requirement Coverage

Target:

```text
>95% requirements mapped
```

### Duplicate Reduction

Target:

```text
Reduce duplicate test cases by 30%+
```

### Manual Excel Dependency

Target:

```text
100% test execution inside platform
```

### Traceability

Target:

```text
100% approved test cases linked to requirements
```

---

# 55. Immediate Next Actions

Start development with these tasks:

### Day 1

- Create Git repository.
- Create frontend project.
- Create backend project.
- Setup SQLite.
- Create database migrations.
- Create Project API.
- Create basic dashboard.

### Day 2

- Implement project creation UI.
- Implement application ZIP upload.
- Implement requirement upload.
- Implement API JSON upload.
- Implement project file storage.

### Day 3

- Build DOCX parser.
- Build Markdown parser.
- Build TXT parser.
- Build requirement extraction service.

### Day 4

- Build secure ZIP analyzer.
- Generate application file inventory.
- Detect framework/project structure.
- Create application analysis JSON.

### Day 5

- Build OpenAPI/API parser.
- Create API catalog.
- Create API analysis JSON.

### Day 6

- Connect AI provider.
- Implement Requirement Analyzer.
- Implement Application Analyzer.
- Implement API Analyzer.

### Day 7

- Build Product Understanding model.
- Implement Test Case Generator.
- Implement Requirement Mapper.
- Add JSON schema validation.

### Day 8

- Build Test Case UI.
- Add search/filter.
- Add edit/approve.
- Add requirement coverage.

### Day 9

- Build Test Execution UI.
- Add Actual Result.
- Add Status.
- Add Screenshot.
- Add Comments.

### Day 10

- Build Bug Tracker.
- Link failed cases to bugs.
- Build dashboard.
- Build Excel export.

### After MVP

- Add AI Test Review.
- Add AI Failure Analysis.
- Add AI Bug Drafting.
- Add MCP tools.
- Add authentication.
- Add PostgreSQL.
- Add object storage.
- Add background workers.
- Add CI/CD.
- Add production security.

---

# 56. Recommended MCP Skill Set

Create these MCP/AI skills:

```text
qa-project-manager
requirement-analyzer
application-code-analyzer
api-document-analyzer
product-understanding-builder
test-case-generator
requirement-test-mapper
test-case-reviewer
test-execution-assistant
failure-analyzer
bug-report-generator
qa-report-generator
coverage-analyzer
excel-report-generator
```

The agent should be able to chain these skills instead of using one large operation.

---

# 57. Final Product Vision

The final workflow should feel like:

```text
Developer
   │
   ├── Application ZIP
   ├── Requirements
   └── API Documentation
           │
           ▼
      QA AI Platform
           │
           ├── Understand Application
           ├── Understand Requirements
           ├── Understand APIs
           ├── Generate Test Cases
           ├── Map Requirements
           ├── Identify Coverage Gaps
           │
           ▼
       QA Engineer
           │
           ├── Review
           ├── Execute
           ├── Capture Evidence
           ├── Mark Result
           └── Raise Bugs
           │
           ▼
       QA Dashboard
           │
           ├── Coverage
           ├── Pass/Fail
           ├── Bugs
           └── Traceability
           │
           ▼
       Excel / QA Report
```

## Core Principle

**AI generates and analyzes. QA validates and approves. The platform maintains traceability and execution history.**

This should be the foundation of the product architecture and development plan.
