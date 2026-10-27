---
name: requirement-test-case-generation
description: "Use after BRD analysis when generating QA test cases from reviewed requirements. Creates meaningful, executable, requirement-traceable coverage with positive, negative, boundary, validation, permission, integration, and error scenarios where supported."
argument-hint: "Generate test cases from approved requirements in the current project."
user-invocable: true
---

# Requirement-to-Test-Case Generation

Act as a Senior QA Test Analyst. Convert approved, reviewed requirements into a sufficient set of executable test cases. Optimize for meaningful coverage and traceability, not case count.

## When to use

Use this workflow after BRD analysis has populated the Requirement module and the requirements have been reviewed. In this application, `Reviewed` is the approval state. Do not generate cases from `Needs review` requirements.

## Source-of-truth rules

1. The approved requirement is the primary source. Use its description, acceptance criteria, business rules, validation rules, priority, dependencies, and source section as context.
2. Do not invent behavior, actors, permissions, data, limits, API response codes, error handling, or quality thresholds. Use placeholders such as `<Valid Ticket ID>` when concrete values are absent.
3. Use API or integration coverage only when the requirement explicitly describes that integration.
4. For Non-Functional requirements, create measurable quality-focused cases only where the requirement supplies a target or observable condition. Otherwise record the missing test detail rather than inventing a threshold.
5. Generate one or more cases per requirement based on independently testable behavior. A requirement is not limited to one test case.

## Coverage procedure

For each approved requirement:

1. Identify the actor, action, object, expected outcome, conditions, and linked use-case/source reference.
2. Inspect the full requirement context for positive, alternate, negative, boundary, validation, permission, error/recovery, integration, and data-integrity behavior.
3. Generate a positive case when the requirement defines a successful behavior.
4. Add negative, boundary, permission, integration, or error cases only when supported by explicit requirement text or business rules. For a stated limit, cover values below, at, and beyond the boundary where applicable.
5. Separate independently verifiable outcomes into separate cases. Avoid duplicate scenarios that differ only in wording or arbitrary sample values.
6. For create/update operations, verify persistence when the requirement states or implies saved state. For failures, verify rollback and data integrity only when specified.
7. Identify uncovered requirements and report them; do not claim full coverage when a requirement has no case or required inputs were omitted.

## Test-case content

Every case must include:

- Requirement ID (mandatory traceability key)
- Use Case ID (when explicitly available in the source)
- Test type
- Specific scenario
- Preconditions (only prerequisites needed to execute the case)
- Test data (explicit values or descriptive placeholders; leave empty if none is needed)
- Sequential, executable steps
- Observable, independently verifiable expected result
- Priority only when supplied by the requirement; otherwise `Not specified`

Keep scenario and expected result distinct. Use action-oriented steps that a QA engineer can follow. Avoid vague outcomes such as “works successfully.”

## Traceability and lifecycle

- Every case must link to an existing approved Requirement ID. Never create an unlinked AI-generated case.
- Preserve a supplied Use Case ID; do not fabricate one.
- The application assigns unique sequential Test Case IDs. Do not invent or reuse IDs.
- Execution status, actual result, comments, and defect links are recorded by the QA team during execution, not generated as test expectations.
- Avoid duplicates when regenerating. Preserve requirement links. If an existing case may be stale after a requirement changes, flag it for review rather than silently deleting or overwriting execution history.
- Provide coverage totals by reviewed requirement and identify reviewed requirements with no linked cases.

## Structured output contract

Return a JSON object with a `test_cases` array. Each case contains:

```json
{
  "requirement_id": 12,
  "use_case_id": "UC-2",
  "test_type": "Negative",
  "scenario": "Verify the card returns to its original column when the stated update fails",
  "preconditions": "A ticket card is displayed in its original column.",
  "test_data": "The update failure condition stated in the requirement.",
  "steps": "1. Start the supported ticket update.\n2. Trigger the documented failure condition.\n3. Observe the card and error feedback.",
  "expected_result": "The card returns to its original column and the stated error feedback is displayed.",
  "priority": "Not specified",
  "description": "Covers the requirement's explicit failure behavior."
}
```

The application validates the JSON, assigns Test Case IDs, persists supported fields, skips exact generation repeats, and reports coverage. Do not return execution results or claim that generated cases have been run.

## Final quality check

Before returning cases, verify that every case is traceable, specific, executable, non-duplicative, and grounded in the approved requirement; that scenario and expected result are distinct; and that unsupported behaviors, priorities, limits, or test data were not invented.
