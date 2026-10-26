"""Bounded extraction and deterministic structuring for requirement documents."""

from __future__ import annotations

import io
import re
import zipfile
import xml.etree.ElementTree as ET
import zlib
from pathlib import Path
from typing import Any


MAX_DOCUMENT_BYTES = 5 * 1024 * 1024
MAX_DOCUMENT_TEXT = 1_000_000
MAX_REQUIREMENTS = 100
MAX_DOCX_ENTRIES = 3000
MAX_DOCX_XML_BYTES = 8 * 1024 * 1024
WORD_NS = {"w": "http://schemas.openxmlformats.org/wordprocessingml/2006/main"}
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
            root = ET.fromstring(xml)
        except (
            zipfile.BadZipFile, OSError, EOFError, RuntimeError,
            NotImplementedError, RecursionError, zlib.error, ET.ParseError,
        ) as exc:
            raise ValueError("Requirement DOCX contains unreadable document content.") from exc

    paragraphs: list[tuple[str, bool]] = []
    for paragraph in root.findall(".//w:p", WORD_NS):
        text = "".join(node.text or "" for node in paragraph.findall(".//w:t", WORD_NS)).strip()
        if not text:
            continue
        style = paragraph.find("./w:pPr/w:pStyle", WORD_NS)
        style_name = style.get(f"{{{WORD_NS['w']}}}val", "") if style is not None else ""
        heading = bool(re.match(r"heading\d+", style_name, re.I))
        paragraphs.append((text, heading))
    return paragraphs


def extract_requirement_document(filename: str, *, content: str | None = None,
                                 document_data: bytes | None = None) -> tuple[str, list[dict[str, Any]]]:
    """Extract text and structure headings, rules, criteria and dependencies."""
    suffix = Path(filename).suffix.lower()
    if suffix == ".docx":
        if not isinstance(document_data, bytes) or not document_data:
            raise ValueError("Requirement DOCX data is missing.")
        if len(document_data) > MAX_DOCUMENT_BYTES:
            raise ValueError("Requirement DOCX is too large (maximum 5 MB).")
        paragraphs = _extract_docx(document_data)
        lines = [(text, heading) for text, heading in paragraphs]
    elif suffix in {".txt", ".md"}:
        if not isinstance(content, str) or not content.strip():
            raise ValueError("Requirement document content is required.")
        if len(content.encode("utf-8")) > MAX_DOCUMENT_TEXT:
            raise ValueError("Requirement document text is too large (maximum 1 MB).")
        lines = [(line.strip(), bool(re.match(r"^\s*#{1,6}\s+", line)))
                 for line in content.splitlines()]
    else:
        raise ValueError("Only DOCX, TXT, and Markdown requirement documents are supported.")

    text = "\n".join(line for line, _ in lines).strip()
    if len(text.encode("utf-8")) > MAX_DOCUMENT_TEXT:
        raise ValueError("Extracted requirement document text is too large (maximum 1 MB).")

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
            "priority": "Medium",
            "source_section": source_section[:200],
        }

    def finish_record() -> None:
        if current is None:
            return
        record = dict(current)
        for field in ("description", "acceptance_criteria", "business_rules",
                      "validation_rules", "dependencies"):
            record[field] = "\n".join(record.pop(f"{field}_lines")).strip()
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
        paragraphs = [line.strip() for line in text.splitlines() if line.strip()]
        records = [new_record(paragraphs[0][:200])]
        records[0]["description"] = text
        records[0]["acceptance_criteria"] = ""
        records[0]["business_rules"] = ""
        records[0]["validation_rules"] = ""
        records[0]["dependencies"] = ""
    elif preamble and records:
        prefix = "\n".join(preamble)
        records[0]["description"] = "\n".join(filter(None, [prefix, records[0]["description"]])).strip()

    if not records:
        raise ValueError("No requirement text was found in this document.")
    if len(records) > MAX_REQUIREMENTS:
        raise ValueError(f"Document contains too many requirements (maximum {MAX_REQUIREMENTS}).")
    return text, records
