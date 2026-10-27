# BRD-to-Requirements Analysis and Skill Redesign

## 1. Objective

Build a product workflow that accepts a developer-provided Business Requirements Document (BRD), extracts its use cases as the primary source requirements, preserves source traceability, and makes the approved requirements available to a separate Test Case Generator.

**Core principle:** A use case is a source requirement unit. Atomic functional requirements, business rules, non-functional requirements, acceptance criteria, and test cases are derived artifacts—not replacements for the original use case.

## 2. Analysis of the current skill

The existing `brd-use-case-requirements` skill is detailed and includes valuable controls for source traceability, requirement atomicity, gap analysis, and NFR classification. However, it makes decomposition into atomic FRs/NFRs the primary output. That can create an unnecessarily transformed view of what the developer supplied and can obscure the original use-case narrative.

### Recommended changes

| Area | Existing behavior | Recommended behavior |
|---|---|---|
| Primary source | Use cases are the source for deriving FRs | Preserve and extract the complete use cases first |
| First output | Atomic FR/NFR report | Source-faithful use-case inventory and scenario steps |
| Wording | Rewrites behaviors into “The system shall…” statements | Retain source wording; add normalized interpretation separately |
| Traceability | Requirement-to-step mapping | BRD section → use case → scenario/flow step → derived requirement → test case |
| Missing details | Gap table | Gap table plus explicit unknown/null values in structured data |
| Derived requirements | Required main deliverable | Optional secondary layer, generated only after source extraction |
| Test cases | Outside this skill | Separate downstream Test Case Generator |
| Approval | Not explicitly modeled | QA review/approval status before test generation |
| Persistence | Markdown report only | Store structured records and produce a readable Markdown report |

### Important distinction

Do not treat every page heading, feature description, implementation note, or goal as a use case. The model must find explicit use-case content. If no use cases exist, it should say so and report any separately stated constraints without manufacturing use cases.

## 3. Evidence from the sample BRD

The provided *Kanban Board by saasly* BRD contains four explicit use cases:

1. **UC-1 — Build and save a board** — Actor: Agent or Admin.
2. **UC-2 — Triage and move tickets** — Actor: Agent.
3. **UC-3 — Work a ticket inline** — Actor: Agent.
4. **UC-4 — Monitor team workload** — Actor: Admin.

Each has a goal and a numbered main scenario. For example, UC-2 includes viewing permitted tickets, applying filters, moving cards, failure rollback and error feedback, creating a ticket in a selected cell, and read-only behavior for date-based columns.

The BRD also has page-wise flow and product details. These should be treated as supporting source material, not automatically promoted into additional use cases. Explicitly stated details can be captured as supporting requirements or constraints with their actual section reference. Unspecified behavior must remain a gap.

## 4. Target workflow

1. **Upload BRD** — retain the original file and metadata.
2. **Parse document** — extract headings, tables, use cases, numbered steps, alternate flows, exception flows, and source locations.
3. **Identify use cases** — detect explicit use-case sections and assign stable IDs when none exist.
4. **Extract source-faithful records** — retain the exact source text for each use case and step. Store actor, goal, preconditions, main flow, alternate flows, exception flows, and stated outcomes where available.
5. **Analyze supporting content** — capture explicit business rules, constraints, and NFRs outside the use-case section, preserving their section references.
6. **Detect gaps** — identify missing actors, conditions, expected results, validation rules, permissions, failure behavior, limits, and acceptance criteria. Do not infer answers.
7. **Generate review report** — present use cases, steps, source references, gaps, and extraction confidence or review flags.
8. **QA review and approval** — allow a reviewer to edit extracted content, confirm source mappings, and approve or reject records.
9. **Optional derived requirements** — generate atomic FR/NFR statements from approved source steps while keeping links to the original use case and step.
10. **Test Case Generator** — consume approved use cases and/or approved derived requirements to generate positive, negative, boundary, and alternate/exception-flow test cases.
11. **Traceability and coverage** — link each test case to its source step and requirement, and show uncovered requirements and steps.

## 5. Suggested architecture

```text
BRD Upload
   |
   v
Document Parser
   |
   v
Use-Case Detector
   |
   v
Gemini BRD Extraction / Analysis
   |
   +--> Source-faithful Use Cases and Steps
   |
   +--> Explicit Business Rules / Constraints / NFRs
   |
   +--> Gaps and Ambiguities
   |
   v
Schema Validation + Traceability Validation
   |
   v
Requirements Repository (draft)
   |
   v
QA Review and Approval
   |
   +-----------------------------+
   |                             |
   v                             v
Approved Use Cases         Optional Derived FR/NFRs
   |                             |
   +--------------+--------------+
                  |
                  v
           Test Case Generator
                  |
                  v
       Test Cases + Traceability
                  |
                  v
       Coverage Report / Export
```

Gemini performs semantic extraction and analysis. Application code should handle document parsing, schema validation, persistence, stable identifiers, approval state, and traceability checks wherever possible. Do not rely on the model alone for structural integrity.

## 6. Suggested structured data model

```json
{
  "brd_id": "BRD-001",
  "document_name": "Kanban Board by saasly",
  "source_version": "2.0",
  "source_document": "brd.md",
  "extraction_status": "draft",
  "use_cases": [
    {
      "use_case_id": "UC-1",
      "source_title": "Build and save a board",
      "title": "Build and save a board",
      "actors": ["Agent", "Admin"],
      "goal": "Create a board that organizes tickets the way their team works",
      "preconditions": [],
      "main_flow": [
        {
          "step_id": "UC-1-S1",
          "sequence": 1,
          "source_text": "User opens the Kanban app from the Zendesk navigation bar and creates a new board",
          "actor_action": "Open the Kanban app and create a board",
          "system_response": null,
          "conditions": [],
          "source_reference": {
            "section": "Use Case",
            "use_case_title": "Use Case 1: Build and save a board",
            "step_label": "1"
          },
          "analysis_status": "needs_review"
        }
      ],
      "alternate_flows": [],
      "exception_flows": [],
      "business_rule_ids": [],
      "gap_ids": [],
      "review_status": "pending"
    }
  ],
  "business_rules": [],
  "nfrs": [],
  "gaps": [],
  "derived_requirements": [],
  "traceability": []
}
```

The example is illustrative. `system_response: null` means the response was not independently captured for that step; the model must not fill it with a customary or assumed behavior. Keep original `source_text` immutable or versioned; store normalized interpretation in separate fields.

## 7. Product requirements for the BRD-to-Requirements feature

### FR-1: BRD ingestion
- FR-1.1 The system shall allow an authorized user to upload a BRD in each supported document format.
- FR-1.2 The system shall retain the source document and its available metadata.
- FR-1.3 The system shall report unsupported, unreadable, or empty documents rather than generating fabricated content.

### FR-2: Use-case extraction
- FR-2.1 The system shall identify explicit use-case sections in the BRD.
- FR-2.2 The system shall extract each identified use case, actor, goal, stated preconditions, and scenario flows where present.
- FR-2.3 The system shall preserve the original wording of each use case and scenario step.
- FR-2.4 The system shall assign stable use-case and step identifiers when source identifiers are absent.
- FR-2.5 The system shall preserve main, alternate, exception, failure, and boundary flows when documented.
- FR-2.6 If no use cases are identified, the system shall explicitly report that no use cases were found and shall not invent them.

### FR-3: Supporting requirement analysis
- FR-3.1 The system shall capture explicitly stated business rules, constraints, and NFRs that occur outside use cases, with their source-section references.
- FR-3.2 The system shall distinguish source-stated facts from model interpretations and unresolved assumptions.
- FR-3.3 The system shall identify missing or ambiguous information that could prevent unambiguous test design.
- FR-3.4 The system shall not invent actors, permissions, validation rules, outcomes, error handling, thresholds, or priorities.

### FR-4: Traceability and validation
- FR-4.1 The system shall map each extracted use case and step to its source location or source heading/step label.
- FR-4.2 The system shall validate the generated output against the configured data schema.
- FR-4.3 The system shall report missing source references, duplicate identifiers, and broken mappings as validation issues.
- FR-4.4 The system shall generate a traceability view from source use case and step to any derived requirement and test case.

### FR-5: Review and approval
- FR-5.1 The system shall present extracted use cases and gaps for human review.
- FR-5.2 The system shall allow a reviewer to edit, approve, or reject extracted records.
- FR-5.3 The system shall retain review status and revisions without overwriting the original source text.
- FR-5.4 The system shall make approval status visible to downstream generation features.

### FR-6: Downstream test generation
- FR-6.1 The system shall make approved use cases and their scenario steps available to the Test Case Generator.
- FR-6.2 The system shall support generation from approved use cases and, where enabled, approved derived requirements.
- FR-6.3 The system shall preserve links from each generated test case to its source use case, step, and any derived requirement.
- FR-6.4 The system shall expose uncovered approved steps or requirements in the coverage report.

### NFRs and unresolved design decisions
The source product BRD must define the implementation quality targets before these can be treated as approved NFRs. The following need product-owner decisions rather than model assumptions:
- Supported file formats, maximum file size, and maximum document length.
- Extraction accuracy/acceptance criteria and how confidence or uncertain extraction is presented.
- Gemini model/API configuration, rate limits, retries, timeout handling, and cost controls.
- Data retention, access control, tenant isolation, and handling of confidential BRDs.
- Expected processing time, concurrency, availability, and recovery behavior.
- Repository technology, versioning, export formats, and audit requirements.
- Whether a human approval is mandatory before test-case generation or configurable by project.
- Whether derived FR/NFR generation is enabled by default or optional.

These are candidate topics for clarification, not requirements already specified by the supplied sample BRD.

## 8. Acceptance criteria for the feature

1. Given a BRD with explicit use cases, when extraction completes, then each identified use case and documented scenario step is represented in the structured result with a source reference.
2. Given a use case with main and alternate/exception flows, when extraction completes, then those flows are preserved and are not silently omitted.
3. Given a BRD with no use-case section or no identifiable use cases, when extraction completes, then the result states that no use cases were found and does not manufacture use cases.
4. Given a step with an unspecified system response, when extraction completes, then the response remains unknown or is flagged as a gap rather than guessed.
5. Given an explicitly stated constraint outside the use-case section, when extraction completes, then it is stored with its actual source section and is not mislabeled as use-case-derived.
6. Given invalid model output, when schema validation runs, then the record is rejected or flagged for correction rather than silently accepted.
7. Given extracted requirements awaiting review, when the user opens the review view, then draft/approved/rejected status is visible.
8. Given approved use cases, when the Test Case Generator runs, then generated test cases link back to the relevant use case and scenario step.
9. Given an approved step with no generated test coverage, when the coverage report is produced, then the uncovered step is identified.
10. The application shall not treat a model-generated interpretation as an explicitly stated source fact unless the source supports it.

## 9. Updated skill prompt

Save as `brd-use-case-requirements.md` or the corresponding skill file used by your product.

---

---
name: brd-use-case-requirements
description: "Use when extracting and analyzing use cases from a developer-provided BRD to create source-faithful, traceable requirements for downstream QA review and test-case generation."
argument-hint: "Analyze the attached or provided BRD. Extract its explicit use cases and scenario steps, preserve source traceability, and report gaps without inventing behavior."
user-invocable: true
---

# BRD Use-Case Extraction and Requirements Analysis

Act as a Business Requirements Analyst and QA Requirements Analyst. The primary task is to extract the use cases actually documented in the supplied BRD and make them available as structured, traceable source requirements for a separate Test Case Generator.

## 1. Primary objective

- Treat each explicit BRD use case as a primary source requirement unit.
- Preserve the original use-case title, actor, goal, preconditions, scenario steps, alternate flows, exception flows, and stated outcomes when present.
- Retain source wording separately from any normalized interpretation.
- Identify missing or ambiguous details without filling them with assumptions.
- Produce structured output suitable for persistence, human review, and downstream test-case generation.
- Do not generate test cases in this skill unless explicitly requested.

## 2. Source-of-truth rules

1. Locate the BRD's `Use Case` or `Use Cases` section. Use each explicitly documented use case as the primary source.
2. Do not infer a use case from a goal, feature heading, page name, product description, architecture note, or implementation detail.
3. Analyze every use case independently and preserve all documented main, alternate, exception, failure, and boundary flows.
4. Use page flows, goals, scope, constraints, and technical notes as supporting context only.
5. Capture explicitly stated business rules, constraints, and NFRs outside use cases in separate collections, citing their actual source sections.
6. If no use cases are found, state that clearly. Do not manufacture use cases or functional behavior from headings.
7. Do not treat model interpretations as facts stated in the BRD. Keep source text, interpretation, and unresolved questions distinguishable.
8. Preserve explicit values, limits, options, conditions, role names, and error behavior exactly.
9. Do not infer priorities, acceptance criteria, permissions, security behavior, validation rules, failure handling, recovery, performance targets, or integrations.
10. Never silently resolve contradictions. Record the conflicting source references as a gap for review.

## 3. Extraction procedure

For the complete document:

1. Record available BRD metadata: document name, product, platform, version, date, and owner. Use `Not specified` when absent.
2. Locate and list explicit use cases in document order.
3. For each use case, extract:
   - Existing source ID, or assign a stable `UC-N` ID.
   - Original title and normalized title, stored separately if normalization is useful.
   - Actor(s), goal, and preconditions, only where stated.
   - Main scenario steps in source order.
   - Alternate, exception, failure, and boundary flows, retaining their labels and step order.
   - Explicit postconditions and outcomes, if present.
   - Source section and step labels or other available source location.
4. For each scenario step, preserve the source text verbatim where practical. Add a separate normalized action/system-response interpretation only when supported by the text.
5. If the step does not state a system response, expected result, condition, or outcome, represent it as unknown/null and/or create a gap. Do not infer it from typical application behavior.
6. Capture explicit business rules, numeric limits, restrictions, and NFRs outside use cases in separate collections. Include exact source references.
7. Identify test-design gaps: missing expected results, validation rules, actors, permissions, preconditions, error/recovery behavior, data constraints, thresholds, or acceptance criteria.
8. Validate IDs, required fields, source mappings, and schema structure before returning the output.
9. Provide a concise QA-readiness assessment for downstream test generation.

## 4. Requirement derivation (secondary, optional)

The use case and scenario steps are the canonical source layer. If atomic derived requirements are requested or enabled by the product configuration:

- Derive each requirement from explicit source behavior only.
- Use “The system shall…” wording for derived requirements.
- Assign stable `FR-N.N` IDs to functional requirements and `NFR-N.N` IDs only to explicitly supported quality requirements.
- Link every derived requirement to the exact use case and source step, or to the actual BRD section for statements outside use cases.
- Do not replace or rewrite the original use case with derived requirements.
- Do not create an NFR merely because a feature mentions an API, database, or technical component.
- If a behavior is ambiguous, record a gap instead of deriving a definitive requirement.
- Use `Not specified` for priority when the BRD does not supply one.

## 5. IDs and traceability

- Preserve source-provided use-case and requirement IDs where available. If absent, assign stable IDs in document order.
- Suggested step IDs: `UC-1-S1`, `UC-1-S2`; alternate and exception steps must have distinct stable labels.
- Suggested derived IDs: `FR-1.1`, `NFR-1.1`, `BR-1`, and `GAP-1`.
- Never reuse an ID for a different record.
- Map every use case and every documented scenario step to a source reference.
- Map derived requirements to their source use case and step.
- Use `No requirement extracted` for steps that do not state a testable system behavior; explain only when helpful.
- Do not claim complete traceability if source locations could not be established.

## 6. Output requirements

Return both:
1. A structured result that follows the configured schema for saving in the requirements repository.
2. A readable Markdown report for QA review.

The Markdown report must contain these sections, in order:

# BRD Requirements Analysis

## 1. BRD Summary
Include document name, product, platform, version, primary actors, and use-case count. Use `Not specified` where necessary.

## 2. Use Cases Identified
For every use case, show ID, original title, actor, goal, stated preconditions, and flow types available.

## 3. Scenario Extraction
For each use case, list all main, alternate, exception, failure, and boundary steps in source order. Show step ID, original source text, supported normalized interpretation, source reference, and any unknown/ambiguous fields.

## 4. Business Rules / Constraints
Include explicit rules and limits only, with source references. Keep assumptions separate.

## 5. Non-Functional Requirements
Include only explicit quality requirements. Cite the actual source section and category. If none are stated, say so.

## 6. Requirement Gaps
Assign stable `GAP-N` IDs. Include missing information, related use case/step, testing impact, and why the detail is needed. Do not propose an invented answer as fact.

## 7. Optional Derived Functional Requirements
Include only if requested or enabled. Use atomic, testable statements and exact source mappings. Otherwise state `Not generated in this analysis`.

## 8. Traceability Matrix
Map each use case and scenario step to source references and any derived requirement IDs. Mark `No requirement extracted` where appropriate.

## 9. QA Readiness
State whether the source is ready for downstream test-case generation, which critical gaps remain, and whether human review is recommended or required by the configured workflow.

## 7. Output schema guidance

Use the application's configured JSON schema. If no schema is supplied, use this conceptual structure:

{
  "brd_metadata": {},
  "use_cases": [
    {
      "use_case_id": "UC-1",
      "source_title": "",
      "title": "",
      "actors": [],
      "goal": null,
      "preconditions": [],
      "main_flow": [
        {
          "step_id": "UC-1-S1",
          "sequence": 1,
          "source_text": "",
          "actor_action": null,
          "system_response": null,
          "expected_outcome": null,
          "source_reference": {},
          "gap_ids": []
        }
      ],
      "alternate_flows": [],
      "exception_flows": [],
      "postconditions": [],
      "source_reference": {},
      "review_status": "pending"
    }
  ],
  "business_rules": [],
  "nfrs": [],
  "gaps": [],
  "derived_requirements": [],
  "traceability": [],
  "qa_readiness": {}
}

This is a conceptual schema. Follow the application's actual schema when one is configured. Use valid JSON when JSON is requested; do not wrap JSON in explanatory prose if the calling application expects machine-readable output only.

## 8. Final quality checklist

Before returning the result, verify:

- Every explicit use case was extracted, and none was invented.
- All documented main, alternate, exception, failure, and boundary steps were considered.
- Original source wording is preserved separately from normalized interpretation.
- Missing details remain unknown or are listed as gaps.
- Explicit constraints, values, and options remain exact.
- Supporting information outside use cases has accurate source references.
- No unsupported NFRs, priorities, expected outcomes, or business rules were added.
- Identifiers are stable and unique.
- Traceability covers every extracted use case and step.
- Structured output validates against the configured schema.
- The report clearly states whether the content is ready for downstream test-case generation.
