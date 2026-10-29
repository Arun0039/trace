"""Bounded extraction and deterministic structuring for requirement documents."""

from __future__ import annotations

import io
import csv
import re
import zipfile
import xml.etree.ElementTree as ET
import zlib

from defusedxml import ElementTree as SafeET
from defusedxml.common import DefusedXmlException
from pathlib import Path
from typing import Any


MAX_DOCUMENT_BYTES = 5 * 1024 * 1024
MAX_DOCUMENT_TEXT = 1_000_000
MAX_REQUIREMENTS = 100
MAX_DOCX_ENTRIES = 3000
MAX_DOCX_XML_BYTES = 8 * 1024 * 1024
MAX_PDF_PAGES = 1000
WORD_NS = {"w": "http://schemas.openxmlformats.org/wordprocessingml/2006/main"}
MODULE_TYPES = ("Functional", "Non-Functional")
NON_FUNCTIONAL_CATEGORIES = (
    "Performance", "Security", "Usability", "Compatibility", "Reliability", "Other",
)
MODULE_CATEGORIES = ("Functional", *NON_FUNCTIONAL_CATEGORIES)
_CATEGORY_HINTS = {
    "Performance": re.compile(
        r"\b(?:performance|latency|response time|throughput|scalab\w*|capacity|"
        r"concurrent users?|high load|requests? per second|rps|p95|p99|"
        r"cpu usage|memory usage|resource usage)\b",
        re.IGNORECASE,
    ),
    "Security": re.compile(
        r"\b(?:security|secure|encrypt\w*|authenticat\w*|authoriz\w*|"
        r"access control|role-based|role based|permission|privacy|"
        r"audit (?:log|trail)|session timeout|session expiry|token|"
        r"password (?:hash|policy|strength)|single sign-on|sso|mfa|2fa|"
        r"vulnerab\w*|cross-site|xss|csrf|sql injection)\b",
        re.IGNORECASE,
    ),
    "Usability": re.compile(
        r"\b(?:usability|user-friendly|user friendly|intuitive|"
        r"accessib\w*|wcag|screen reader|locali[sz]\w*|translation|"
        r"responsive|mobile-friendly|mobile friendly|learnab\w*|"
        r"consistent (?:ui|navigation|layout)|ease of use)\b",
        re.IGNORECASE,
    ),
    "Compatibility": re.compile(
        r"\b(?:compatib\w*|portab\w*|browser|chrome|firefox|safari|edge|"
        r"operating system|windows|macos|linux|ios|android|tablet|"
        r"mobile device|screen resolution|viewport)\b",
        re.IGNORECASE,
    ),
    "Reliability": re.compile(
        r"\b(?:reliab\w*|availability|available|uptime|backup|recover\w*|"
        r"failover|failover|retry|resilien\w*|fault[- ]toleran\w*|"
        r"disaster recovery|data integrity|data consistency|"
        r"sla|99\.9|high availability)\b",
        re.IGNORECASE,
    ),
}
_CATEGORY_ALIASES = {
    "functional": "Functional",
    "function": "Functional",
    "fr": "Functional",
    "non-functional": "Non-Functional",
    "non functional": "Non-Functional",
    "nonfunctional": "Non-Functional",
    "nfr": "Non-Functional",
    "quality": "Non-Functional",
}
REQUIREMENT_HEADING = re.compile(
    r"^\s*(?:#{1,6}\s+)?(?:(?:requirement\s+)?(REQ[-_ ]?\d+)\s*(?:[:.\-]\s*|\s+)|"
    r"\[(REQ[-_ ]?\d+)\]\s*)(.*)$",
    re.IGNORECASE,
)
SECTION_HEADINGS = {
    "acceptance criteria": "acceptance_criteria",
    "acceptance criterion": "acceptance_criteria",
    "business rules": "business_rules",
    "business rule": "business_rules",
    "validation rules": "validation_rules",
    "validation": "validation_rules",
    "dependencies": "dependencies",
    "dependency": "dependencies",
}
PRIORITY_PATTERN = re.compile(r"^\s*priority\s*:\s*(high|medium|low)\s*$", re.I)
REQUIREMENT_STATEMENT = re.compile(
    r"\b(?:shall|must|should|may|can|will|need(?:s)?(?:\s+\w+){0,3}\s+to|"
    r"is required to)\b",
    re.IGNORECASE,
)


def classify_requirement_text(*parts: str) -> tuple[str, str]:
    """Return (module type, module category) inferred from requirement text."""
    text = "\n".join(part for part in parts if isinstance(part, str) and part)
    if not text.strip():
        return "Functional", "Functional"
    scores = {category: len(pattern.findall(text)) for category, pattern in _CATEGORY_HINTS.items()}
    order = list(_CATEGORY_HINTS)
    best = max(order, key=lambda category: (scores[category], -order.index(category)))
    if scores[best] > 0:
        return "Non-Functional", best
    return "Functional", "Functional"


def normalize_requirement_module(
    module_type: Any,
    module_category: Any,
    *fallback_parts: str,
) -> tuple[str, str]:
    """Normalize AI/user module values, inferring a safe classification when needed."""
    type_value = module_type.strip().casefold() if isinstance(module_type, str) else ""
    category_value = module_category.strip().casefold() if isinstance(module_category, str) else ""
    type_value = _CATEGORY_ALIASES.get(type_value, type_value)
    category_value = _CATEGORY_ALIASES.get(category_value, category_value)
    exact_category = next(
        (category for category in MODULE_CATEGORIES if category.casefold() == category_value),
        "",
    )
    if type_value == "Functional" or exact_category == "Functional":
        return "Functional", "Functional"
    if type_value == "Non-Functional" or exact_category:
        if exact_category and exact_category != "Functional":
            return "Non-Functional", exact_category
        inferred_type, inferred_category = classify_requirement_text(*fallback_parts)
        if inferred_type == "Non-Functional":
            return inferred_type, inferred_category
        return "Non-Functional", "Other"
    return classify_requirement_text(*fallback_parts)


def _extract_docx(data: bytes) -> list[tuple[str, bool]]:
    try:
        archive = zipfile.ZipFile(io.BytesIO(data))
    except (zipfile.BadZipFile, OSError, ValueError) as exc:
        raise ValueError("Requirement DOCX is not a valid document.") from exc
    with archive:
        entries = archive.infolist()
        if len(entries) > MAX_DOCX_ENTRIES:
            raise ValueError("Requirement DOCX contains too many entries.")
        names: set[str] = set()
        for entry in entries:
            name = entry.filename.casefold()
            if name in names:
                raise ValueError("Requirement DOCX contains duplicate ZIP entries.")
            names.add(name)
        try:
            document = archive.getinfo("word/document.xml")
        except KeyError as exc:
            raise ValueError("Requirement DOCX has no document content.") from exc
        if document.flag_bits & 0x1 or document.file_size > MAX_DOCX_XML_BYTES:
            raise ValueError("Requirement DOCX document content is encrypted or too large.")
        try:
            chunks: list[bytes] = []
            total = 0
            with archive.open(document) as source:
                while chunk := source.read(64 * 1024):
                    total += len(chunk)
                    if total > MAX_DOCX_XML_BYTES:
                        raise ValueError("Requirement DOCX document content is too large.")
                    chunks.append(chunk)
            xml = b"".join(chunks)
            root = SafeET.fromstring(xml)
        except (
            zipfile.BadZipFile, OSError, EOFError, RuntimeError,
            NotImplementedError, RecursionError, zlib.error, ET.ParseError,
            DefusedXmlException,
        ) as exc:
            raise ValueError("Requirement DOCX contains unreadable document content.") from exc

    paragraphs: list[tuple[str, bool]] = []
    body = root.find(".//w:body", WORD_NS)
    if body is None:
        raise ValueError("Requirement DOCX has no document content.")
    for element in body:
        if element.tag == f"{{{WORD_NS['w']}}}p":
            paragraph = element
            text = "".join(node.text or "" for node in paragraph.findall(".//w:t", WORD_NS)).strip()
            if not text:
                continue
            style = paragraph.find("./w:pPr/w:pStyle", WORD_NS)
            style_name = style.get(f"{{{WORD_NS['w']}}}val", "") if style is not None else ""
            paragraphs.append((text, bool(re.match(r"heading\d+", style_name, re.I))))
        elif element.tag == f"{{{WORD_NS['w']}}}tbl":
            for row in element.findall("./w:tr", WORD_NS):
                cells = [
                    " ".join(
                        "".join(node.text or "" for node in paragraph.findall(".//w:t", WORD_NS)).strip()
                        for paragraph in cell.findall("./w:p", WORD_NS)
                    ).strip()
                    for cell in row.findall("./w:tc", WORD_NS)
                ]
                row_text = " | ".join(cell for cell in cells if cell)
                if row_text:
                    paragraphs.append((row_text, False))
    return paragraphs


def _extract_pdf(data: bytes) -> list[tuple[str, bool]]:
    try:
        from pypdf import PdfReader
        from pypdf.errors import PdfReadError
    except ImportError as exc:
        raise RuntimeError("PDF support requires the pypdf dependency; install requirements.txt.") from exc
    try:
        reader = PdfReader(io.BytesIO(data), strict=True)
        if reader.is_encrypted:
            raise ValueError("Encrypted requirement PDFs are not supported.")
        if len(reader.pages) > MAX_PDF_PAGES:
            raise ValueError(f"Requirement PDF contains too many pages (maximum {MAX_PDF_PAGES}).")
        pages = []
        total_text_bytes = 0
        for index, page in enumerate(reader.pages, 1):
            page_text = page.extract_text() or ""
            total_text_bytes += len(page_text.encode("utf-8"))
            if total_text_bytes > MAX_DOCUMENT_TEXT:
                raise ValueError("Extracted requirement PDF text is too large (maximum 1 MB).")
            pages.append((f"[Page {index}] {page_text}".strip(), False))
    except (PdfReadError, OSError, EOFError, zlib.error, RecursionError) as exc:
        raise ValueError("Requirement PDF is invalid or unreadable.") from exc
    if not any(text.removeprefix("[Page ").split("] ", 1)[-1].strip() for text, _ in pages):
        raise ValueError("Requirement PDF contains no extractable text; OCR is not supported.")
    return pages


def extract_requirement_document(filename: str, *, content: str | None = None,
                                 document_data: bytes | None = None) -> tuple[str, list[dict[str, Any]]]:
    """Extract text and structure headings, rules, criteria and dependencies."""
    suffix = Path(filename).suffix.lower()
    if suffix in {".docx", ".pdf"}:
        if not isinstance(document_data, bytes) or not document_data:
            raise ValueError(f"Requirement {suffix[1:].upper()} data is missing.")
        if len(document_data) > MAX_DOCUMENT_BYTES:
            raise ValueError(f"Requirement {suffix[1:].upper()} is too large (maximum 5 MB).")
        paragraphs = _extract_docx(document_data) if suffix == ".docx" else _extract_pdf(document_data)
        if suffix == ".pdf":
            lines = []
            for page_text, _ in paragraphs:
                page = re.match(r"^\[Page (\d+)\]\s*", page_text)
                page_number = page.group(1) if page else ""
                content = page_text[page.end():] if page else page_text
                lines.extend(
                    (f"[Page {page_number}] {line.strip()}", False)
                    for line in content.splitlines() if line.strip()
                )
        else:
            lines = [(text, heading) for text, heading in paragraphs]
    elif suffix in {".txt", ".md", ".csv"}:
        if not isinstance(content, str) or not content.strip():
            raise ValueError("Requirement document content is required.")
        if len(content.encode("utf-8")) > MAX_DOCUMENT_TEXT:
            raise ValueError("Requirement document text is too large (maximum 1 MB).")
        if suffix == ".csv":
            try:
                rows = csv.reader(io.StringIO(content))
                lines = [(" | ".join(cell.strip() for cell in row), False) for row in rows]
            except csv.Error as exc:
                raise ValueError("Requirement CSV contains invalid rows.") from exc
        else:
            lines = [(line.strip(), bool(re.match(r"^\s*#{1,6}\s+", line)))
                     for line in content.splitlines()]
    else:
        raise ValueError("Only PDF, DOCX, CSV, TXT, and Markdown requirement documents are supported.")

    text = "\n".join(line for line, _ in lines).strip()
    if len(text.encode("utf-8")) > MAX_DOCUMENT_TEXT:
        raise ValueError("Extracted requirement document text is too large (maximum 1 MB).")

    if suffix == ".csv":
        rows = list(csv.reader(io.StringIO(content or "")))
        if rows:
            headers = [cell.strip().casefold() for cell in rows[0]]

            def column(row: list[str], *names: str) -> str:
                for name in names:
                    if name in headers:
                        index = headers.index(name)
                        if index < len(row):
                            return row[index].strip()
                return ""

            records = []
            for row_number, row in enumerate(rows[1:], 2):
                title = column(
                    row, "title", "name", "feature", "use case", "user story",
                    "requirement", "requirement title",
                )
                description = column(
                    row, "description", "details", "statement",
                    "requirement description",
                )
                acceptance_criteria = column(
                    row, "acceptance criteria", "acceptance criterion", "criteria",
                )
                business_rules = column(row, "business rules", "business rule")
                validation_rules = column(row, "validation rules", "validation")
                dependencies = column(row, "dependencies", "dependency")
                if not title or not any((
                    description, acceptance_criteria, business_rules,
                    validation_rules, dependencies,
                )):
                    continue
                key = column(row, "requirement id", "requirement key", "id", "key")
                priority = column(row, "priority").casefold()
                priority = {
                    "critical": "High", "p0": "High", "p1": "High",
                    "p2": "Medium", "p3": "Low",
                    "high": "High", "medium": "Medium", "low": "Low",
                }.get(priority, "Not specified")
                record = {
                    "requirement_key": key.upper() if re.fullmatch(r"REQ-\d+", key, re.I) else "",
                    "title": title[:200],
                    "description": description,
                    "acceptance_criteria": acceptance_criteria,
                    "business_rules": business_rules,
                    "validation_rules": validation_rules,
                    "dependencies": dependencies,
                    "priority": priority,
                    "module_type": "",
                    "module_category": "",
                    "source_section": f"CSV row {row_number}",
                    "source_evidence": " | ".join(cell.strip() for cell in row)[:12000],
                }
                record["module_type"], record["module_category"] = normalize_requirement_module(
                    "", "", title, description, acceptance_criteria,
                    business_rules, validation_rules, dependencies,
                )
                records.append(record)
            if len(records) > MAX_REQUIREMENTS:
                raise ValueError(f"Document contains too many requirements (maximum {MAX_REQUIREMENTS}).")
            return text, records

    records: list[dict[str, Any]] = []
    current: dict[str, Any] | None = None
    section = "description"
    preamble: list[str] = []

    def new_record(title: str, requirement_key: str = "", source_section: str = "") -> dict[str, Any]:
        return {
            "requirement_key": requirement_key,
            "title": title[:200] or "Requirement",
            "description_lines": [],
            "acceptance_criteria_lines": [],
            "business_rules_lines": [],
            "validation_rules_lines": [],
            "dependencies_lines": [],
            "priority": "Not specified",
            "module_type": "",
            "module_category": "",
            "source_section": source_section[:200],
        }

    def finish_record() -> None:
        if current is None:
            return
        record = dict(current)
        for field in ("description", "acceptance_criteria", "business_rules",
                      "validation_rules", "dependencies"):
            record[field] = "\n".join(record.pop(f"{field}_lines")).strip()
        evidence = "\n".join(
            part for part in (
                record["title"],
                record["description"],
                record["acceptance_criteria"],
                record["business_rules"],
                record["validation_rules"],
                record["dependencies"],
            ) if part
        )
        body = "\n".join(
            record[field] for field in (
                "description", "acceptance_criteria", "business_rules",
                "validation_rules", "dependencies",
            )
        )
        explicit_structure = bool(
            record["requirement_key"]
            or re.match(r"^(?:use case|user story|feature|functional requirement)\b",
                        record["title"], re.I)
        )
        structured_detail = any(
            record[field] for field in (
                "acceptance_criteria", "business_rules",
                "validation_rules", "dependencies",
            )
        )
        if not (
            REQUIREMENT_STATEMENT.search(body)
            or (explicit_structure and body.strip())
            or structured_detail
        ):
            return
        if not any(
            record[field] for field in (
                "description", "acceptance_criteria", "business_rules",
                "validation_rules", "dependencies",
            )
        ):
            return
        record["source_evidence"] = evidence[:12000]
        record["module_type"], record["module_category"] = normalize_requirement_module(
            record.get("module_type"),
            record.get("module_category"),
            record["title"],
            record["description"],
            record["acceptance_criteria"],
            record["business_rules"],
            record["validation_rules"],
            record["dependencies"],
        )
        records.append(record)

    for line, is_heading in lines:
        if not line:
            continue
        match = REQUIREMENT_HEADING.match(line)
        if match:
            finish_record()
            requirement_key = (match.group(1) or match.group(2) or "").upper().replace(" ", "-").replace("_", "-")
            title = (match.group(3) or "").strip().strip("#").strip() or requirement_key
            current = new_record(title, requirement_key, title)
            section = "description"
            continue

        heading_text = re.sub(r"^\s*#{1,6}\s*", "", line).strip().rstrip(":").strip().lower()
        if heading_text in SECTION_HEADINGS and current is not None:
            section = SECTION_HEADINGS[heading_text]
            continue

        if is_heading:
            finish_record()
            title = re.sub(r"^\s*#{1,6}\s*", "", line).strip()
            current = new_record(title, source_section=title)
            section = "description"
            continue

        priority = PRIORITY_PATTERN.match(line)
        if priority and current is not None:
            current["priority"] = priority.group(1).title()
            continue
        if current is None:
            preamble.append(line)
            continue

        normalized = re.sub(r"^\s*(?:[-*+]\s+|\d+[.)]\s+)", "", line).strip()
        if normalized:
            current[f"{section}_lines"].append(normalized)

    finish_record()
    if not records and text:
        for line_number, source_line in enumerate(text.splitlines(), 1):
            page = re.match(r"^\[Page (\d+)\]\s*", source_line)
            source_location = (
                f"Page {page.group(1)}, line {line_number}"
                if page else f"Line {line_number}"
            )
            evidence_line = source_line[page.end():].strip() if page else source_line.strip()
            statement = re.sub(
                r"^\s*(?:[-*+]\s+|\d+[.)]\s+)", "", evidence_line
            ).strip()
            if not statement or not REQUIREMENT_STATEMENT.search(statement):
                continue
            record = new_record(
                statement.rstrip(".")[:200],
                source_section=source_location,
            )
            record["description"] = statement
            record["source_evidence"] = evidence_line[:12000]
            record["module_type"], record["module_category"] = normalize_requirement_module(
                "",
                "",
                record["title"],
                record["description"],
            )
            records.append(record)
    elif preamble and records:
        prefix = "\n".join(preamble)
        records[0]["description"] = "\n".join(filter(None, [prefix, records[0]["description"]])).strip()
        records[0]["source_evidence"] = "\n".join(filter(
            None, [records[0].get("source_evidence", ""), prefix]
        ))[:12000]

    for record in records:
        if not record.get("module_type"):
            record["module_type"], record["module_category"] = normalize_requirement_module(
                record.get("module_type"),
                record.get("module_category"),
                record.get("title", ""),
                record.get("description", ""),
                record.get("acceptance_criteria", ""),
                record.get("business_rules", ""),
                record.get("validation_rules", ""),
                record.get("dependencies", ""),
            )
    if len(records) > MAX_REQUIREMENTS:
        raise ValueError(f"Document contains too many requirements (maximum {MAX_REQUIREMENTS}).")
    return text, records
