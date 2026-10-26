"""Generate deterministic, requirement-linked draft test cases."""

from __future__ import annotations

import hashlib
import re
from typing import Any

MAX_GENERATED_CASES = 300
MAX_RULE_LENGTH = 2000
TEST_TYPES = (
    "Functional", "Negative", "Boundary", "Validation", "Permission",
    "API", "Integration", "Performance", "Security", "Usability",
    "Compatibility", "Reliability", "Non-Functional",
)
NON_FUNCTIONAL_TEST_TYPES = {
    "Performance", "Security", "Usability", "Compatibility", "Reliability",
}

_BOUNDARY_HINTS = re.compile(
    r"\b(minimum|maximum|min|max|at least|at most|between|range|length|limit|boundary|zero)\b|"
    r"\b(?:after|within|up to|more than|fewer than|greater than|less than)\s+\d+",
    re.IGNORECASE,
)
_NEGATIVE_HINTS = re.compile(
    r"\b(invalid|reject|error|missing|required|not allowed|unauthori[sz]ed|"
    r"duplicate|empty|incorrect|fail|validation)\b",
    re.IGNORECASE,
)
_PERMISSION_HINTS = re.compile(
    r"\b(role|permission|authorized|authorised|access control|admin|read.only)\b",
    re.IGNORECASE,
)
_API_HINTS = re.compile(
    r"\b(api|endpoint|http|request body|response code|status code|rest)\b",
    re.IGNORECASE,
)
_INTEGRATION_HINTS = re.compile(
    r"\b(integration|external service|third.party|provider|synchroni[sz]e|"
    r"dependency|webhook)\b",
    re.IGNORECASE,
)


def _items(value: str) -> list[str]:
    items = []
    for line in value.splitlines():
        text = re.sub(r"^\s*(?:[-*+]\s+|\d+[.)]\s+)", "", line).strip()
        if text:
            items.append(text[:MAX_RULE_LENGTH])
    return items


def _case_type(text: str, *, validation: bool = False, business_rule: bool = False) -> str:
    if _BOUNDARY_HINTS.search(text):
        return "Boundary"
    if _PERMISSION_HINTS.search(text):
        return "Permission"
    if _API_HINTS.search(text):
        return "API"
    if _INTEGRATION_HINTS.search(text):
        return "Integration"
    if validation:
        return "Validation"
    if _NEGATIVE_HINTS.search(text):
        return "Negative"
    if business_rule:
        return "Functional"
    return "Functional"


def _module_case_type(requirement: dict[str, Any]) -> str:
    if str(requirement.get("module_type") or "").casefold() != "non-functional":
        return ""
    category = str(requirement.get("module_category") or "")
    return category if category in NON_FUNCTIONAL_TEST_TYPES else "Non-Functional"


def _fingerprint(requirement_id: int, case_type: str, source_text: str) -> str:
    normalized = " ".join(source_text.casefold().split())
    value = f"{requirement_id}\0{case_type}\0{normalized}".encode("utf-8")
    return hashlib.sha256(value).hexdigest()


def _source_fingerprint(source_type: str, source_text: str) -> str:
    normalized = " ".join(source_text.casefold().split())
    return hashlib.sha256(f"{source_type}\0{normalized}".encode("utf-8")).hexdigest()


def _requirement_for_source(source_text: str, requirements: list[dict[str, Any]]) -> dict[str, Any] | None:
    tokens = {
        token for token in re.findall(r"[a-z0-9]{3,}", source_text.casefold())
        if token not in {"get", "post", "put", "patch", "delete", "api", "the", "and", "for"}
    }
    if not tokens:
        return None
    ranked: list[tuple[int, dict[str, Any]]] = []
    for requirement in requirements:
        text = " ".join(
            str(requirement.get(field) or "")
            for field in ("title", "description", "acceptance_criteria")
        ).casefold()
        overlap = sum(1 for token in tokens if re.search(rf"\b{re.escape(token)}\b", text))
        if overlap:
            ranked.append((overlap, requirement))
    if not ranked:
        return None
    ranked.sort(key=lambda item: (-item[0], int(item[1]["id"])))
    if len(ranked) > 1 and ranked[0][0] == ranked[1][0]:
        return None
    return ranked[0][1]


def generate_requirement_cases(requirement: dict[str, Any]) -> list[dict[str, str]]:
    """Create draft cases only from explicit requirement text and criteria."""
    requirement_id = int(requirement["id"])
    requirement_key = requirement["requirement_key"]
    title = requirement["title"].strip()
    groups = (
        ("acceptance_criteria", False, False),
        ("business_rules", False, True),
        ("validation_rules", True, False),
    )
    sources: list[tuple[str, str, bool, bool]] = []
    for field, is_validation, is_business_rule in groups:
        for item in _items(requirement.get(field) or ""):
            sources.append((field, item, is_validation, is_business_rule))

    if not sources:
        description = requirement.get("description", "").strip()
        sources.append(("description", description or title, False, False))

    result = []
    module_case_type = _module_case_type(requirement)
    for field, source_text, is_validation, is_business_rule in sources[:MAX_GENERATED_CASES]:
        case_type = module_case_type or _case_type(
            source_text, validation=is_validation, business_rule=is_business_rule
        )
        if module_case_type:
            label = module_case_type if module_case_type != "Non-Functional" else "non-functional"
            scenario = f"Verify {label.lower()} requirement: {source_text}"
            steps = (
                f"1. Prepare the environment described for {title}.\n"
                f"2. Exercise this condition: {source_text}\n"
                f"3. Observe or measure the result against the stated quality target."
            )
            expected = (
                f"The system meets the stated {label.lower()} requirement: {source_text}"
            )
        elif is_validation:
            scenario = f"Verify validation: {source_text}"
            steps = (
                f"1. Open the feature for {title}.\n"
                f"2. Provide input that violates this rule: {source_text}\n"
                "3. Submit the action and observe the response."
            )
            expected = (
                f"The system prevents the invalid action and communicates the rule: {source_text}"
            )
        elif is_business_rule:
            scenario = f"Verify business rule: {source_text}"
            steps = (
                f"1. Open the feature for {title}.\n"
                f"2. Perform the action relevant to this rule: {source_text}\n"
                "3. Observe the resulting behavior."
            )
            expected = f"The behavior follows the stated business rule: {source_text}"
        else:
            scenario = (
                f"Verify acceptance criterion: {source_text}"
                if field == "acceptance_criteria"
                else f"Verify requirement: {title}"
            )
            steps = (
                f"1. Open the feature or workflow for {title}.\n"
                f"2. Perform the action described by: {source_text}\n"
                "3. Observe the result."
            )
            expected = source_text

        result.append({
            "scenario": scenario[:200],
            "description": f"Draft generated from {requirement_key}: {source_text}",
            "steps": steps,
            "expected_result": expected,
            "test_type": case_type,
            "generation_key": _fingerprint(requirement_id, case_type, f"{field}:{source_text}"),
        })
    return result


def generate_project_cases(
    requirements: list[dict[str, Any]],
    api_endpoints: list[dict[str, Any]],
    application_analysis: dict[str, Any],
) -> list[dict[str, Any]]:
    """Combine parsed requirements, API operations, and static source routes."""
    generated: list[dict[str, Any]] = []
    for requirement in requirements:
        generated.extend(
            {**case, "requirement_id": int(requirement["id"])}
            for case in generate_requirement_cases(requirement)
        )

    for endpoint in api_endpoints:
        method = str(endpoint.get("method") or "").upper()
        path = str(endpoint.get("path") or "")
        if not method or not path:
            continue
        operation = str(endpoint.get("summary") or endpoint.get("operation_id") or "").strip()
        source = f"{method} {path} {operation}".strip()
        requirement = _requirement_for_source(source, requirements)
        generated.append({
            "scenario": f"Verify API {method} {path}"[:200],
            "description": (
                f"Draft generated from the imported API specification"
                f"{': ' + operation if operation else ''}. Confirm request and response details against the contract."
            )[:12000],
            "steps": (
                f"1. Prepare a valid request for {method} {path} using the API contract.\n"
                "2. Send the request to the configured test environment.\n"
                "3. Compare the response status and body with the documented contract."
            ),
            "expected_result": (
                f"The endpoint {method} {path} responds according to its documented contract."
            ),
            "test_type": "API",
            "generation_key": _source_fingerprint("api", source),
            "requirement_id": int(requirement["id"]) if requirement else None,
        })

    routes = application_analysis.get("routes", [])
    if isinstance(routes, list):
        for route_item in routes:
            route = str(route_item.get("route") or "").strip()
            if not route:
                continue
            source = f"{route_item.get('file', '')}:{route_item.get('line', '')}:{route}"
            requirement = _requirement_for_source(route, requirements)
            generated.append({
                "scenario": f"Verify application route {route}"[:200],
                "description": (
                    f"Draft generated from a static source-code route candidate in "
                    f"{route_item.get('file', 'source code')}. Verify the route manually."
                ),
                "steps": (
                    f"1. Open the application in the test environment.\n"
                    f"2. Navigate to route {route}.\n"
                    "3. Observe the page and any resulting navigation."
                ),
                "expected_result": f"Route {route} resolves to the expected application view.",
                "test_type": "Functional",
                "generation_key": _source_fingerprint("source-route", source),
                "requirement_id": int(requirement["id"]) if requirement else None,
            })
    return generated
