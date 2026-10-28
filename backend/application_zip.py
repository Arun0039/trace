"""Safe extraction and bounded, static inventory of untrusted application ZIPs."""

from __future__ import annotations

import json
import io
import os
import re
import shutil
import stat
import zipfile
import zlib
from collections import Counter
from pathlib import Path
from typing import Any
from uuid import uuid4


MAX_ARCHIVE_BYTES = 25 * 1024 * 1024
MAX_ENTRIES = 3000
MAX_TOTAL_UNCOMPRESSED = 100 * 1024 * 1024
MAX_FILE_UNCOMPRESSED = 20 * 1024 * 1024
MAX_COMPRESSION_RATIO = 200
MAX_SOURCE_FILES = 500
MAX_SOURCE_FILE_BYTES = 256 * 1024
MAX_SOURCE_TOTAL_BYTES = 8 * 1024 * 1024
MAX_FINDINGS = 500
SOURCE_EXTENSIONS = {
    ".js", ".jsx", ".mjs", ".cjs", ".ts", ".tsx", ".vue", ".svelte",
    ".py", ".html", ".htm", ".rb", ".php", ".java", ".cs", ".go",
}
CONFIG_NAMES = {
    "package.json", "pyproject.toml", "requirements.txt", "pom.xml",
    "build.gradle", "build.gradle.kts", "cargo.toml", "go.mod",
    "composer.json", "angular.json", "vite.config.js", "vite.config.ts",
}
TYPE_BY_EXTENSION = {
    ".js": "JavaScript", ".jsx": "JavaScript UI", ".mjs": "JavaScript",
    ".cjs": "JavaScript", ".ts": "TypeScript", ".tsx": "TypeScript UI",
    ".vue": "Vue component", ".svelte": "Svelte component",
    ".py": "Python", ".html": "HTML", ".htm": "HTML", ".css": "Stylesheet",
    ".scss": "Stylesheet", ".sass": "Stylesheet", ".less": "Stylesheet",
    ".json": "JSON", ".md": "Markdown", ".txt": "Text",
    ".yaml": "Configuration", ".yml": "Configuration", ".toml": "Configuration",
    ".xml": "XML", ".java": "Java", ".cs": "C#", ".go": "Go",
    ".rb": "Ruby", ".php": "PHP", ".sql": "SQL",
    ".png": "Image", ".jpg": "Image", ".jpeg": "Image", ".gif": "Image",
    ".webp": "Image", ".svg": "Image", ".ico": "Image",
    ".zip": "Archive (not extracted)", ".asar": "Archive (not extracted)",
}

ROUTE_PATTERNS = (
    re.compile(r"""(?:app|router)\s*\.\s*(?:get|post|put|patch|delete|route)\s*\(\s*['"`]([^'"`]+)""", re.I),
    re.compile(r"""@(?:app|router)\s*\.\s*(?:get|post|put|patch|delete)\s*\(\s*['"]([^'"]+)""", re.I),
    re.compile(r"""<Route\b[^>]*\bpath\s*=\s*["'{]([^"'}]+)""", re.I),
    re.compile(r"""\bpath\s*:\s*['"]([^'"]+)['"]""", re.I),
)
API_PATTERNS = (
    re.compile(r"\bfetch\s*\(", re.I),
    re.compile(r"\baxios(?:\s*\.\s*(?:get|post|put|patch|delete|create)|\s*\()", re.I),
    re.compile(r"\b(?:graphql|gql)\s*`", re.I),
    re.compile(r"\b(?:requests|httpx)\s*\.\s*(?:get|post|put|patch|delete)\s*\(", re.I),
    re.compile(r"\b(?:HttpClient|WebClient)\b"),
)
COMPONENT_PATTERN = re.compile(
    r"(?:export\s+default\s+)?(?:function|class)\s+([A-Z][A-Za-z0-9_]*)|"
    r"(?:export\s+)?const\s+([A-Z][A-Za-z0-9_]*)\s*=\s*(?:\([^)]*\)\s*=>|function\b)",
)
WINDOWS_RESERVED = re.compile(r"^(?:CON|PRN|AUX|NUL|COM[1-9]|LPT[1-9])(?:\..*)?$", re.I)


def _safe_member_name(raw_name: str) -> tuple[str, bool]:
    if not raw_name or "\x00" in raw_name:
        raise ValueError("ZIP contains an empty or invalid file path.")
    normalized = raw_name.replace("\\", "/")
    is_directory = normalized.endswith("/")
    if normalized.startswith("/") or normalized.startswith("//") or re.match(r"^[A-Za-z]:", normalized):
        raise ValueError(f"ZIP contains an absolute path: {raw_name[:120]}")
    parts = normalized.rstrip("/").split("/")
    if not parts or any(part in ("", ".", "..") for part in parts):
        raise ValueError(f"ZIP contains an unsafe path: {raw_name[:120]}")
    if any(":" in part or part.endswith((".", " ")) or WINDOWS_RESERVED.fullmatch(part) for part in parts):
        raise ValueError(f"ZIP contains a platform-specific unsafe path: {raw_name[:120]}")
    if any(any(ord(char) < 32 for char in part) for part in parts):
        raise ValueError("ZIP contains a path with control characters.")
    safe_name = "/".join(parts)
    if len(safe_name) > 240:
        raise ValueError("ZIP contains a path longer than 240 characters.")
    return safe_name, is_directory


def _validated_members(archive: zipfile.ZipFile) -> list[tuple[zipfile.ZipInfo, str, bool]]:
    members = archive.infolist()
    if len(members) > MAX_ENTRIES:
        raise ValueError(f"ZIP contains too many entries (maximum {MAX_ENTRIES}).")
    validated: list[tuple[zipfile.ZipInfo, str, bool]] = []
    names: dict[str, bool] = {}
    total_size = 0
    for info in members:
        safe_name, is_directory = _safe_member_name(info.filename)
        if info.flag_bits & 0x1:
            raise ValueError("Encrypted ZIP entries are not supported.")
        if info.compress_type not in (zipfile.ZIP_STORED, zipfile.ZIP_DEFLATED):
            raise ValueError("ZIP uses an unsupported compression method.")
        unix_mode = (info.external_attr >> 16) & 0xFFFF
        file_kind = stat.S_IFMT(unix_mode)
        if file_kind not in (0, stat.S_IFREG, stat.S_IFDIR):
            raise ValueError("ZIP contains a symbolic link or special file.")
        if is_directory and file_kind == stat.S_IFREG:
            raise ValueError("ZIP directory has file metadata.")
        if is_directory and info.file_size != 0:
            raise ValueError("ZIP directory entry contains file data.")
        if not is_directory and file_kind == stat.S_IFDIR:
            raise ValueError("ZIP file has directory metadata.")
        if info.file_size < 0 or info.file_size > MAX_FILE_UNCOMPRESSED:
            raise ValueError("ZIP contains a file larger than 20 MB.")
        if not is_directory:
            if info.file_size and not info.compress_size:
                raise ValueError("ZIP contains an invalid compressed file.")
            ratio = info.file_size / max(info.compress_size, 1)
            if ratio > MAX_COMPRESSION_RATIO:
                raise ValueError("ZIP contains an excessive compression ratio.")
            total_size += info.file_size
            if total_size > MAX_TOTAL_UNCOMPRESSED:
                raise ValueError("ZIP expands beyond the 100 MB safety limit.")
        canonical_name = safe_name.casefold()
        if canonical_name in names:
            raise ValueError(f"ZIP contains duplicate paths: {safe_name[:120]}")
        names[canonical_name] = is_directory
        validated.append((info, safe_name, is_directory))

    for _, name, is_directory in validated:
        parts = name.split("/")
        for index in range(1, len(parts)):
            parent = "/".join(parts[:index]).casefold()
            if names.get(parent) is False:
                raise ValueError("ZIP file path conflicts with a parent file.")
        if not is_directory:
            prefix = name.casefold() + "/"
            if any(other.startswith(prefix) for other in names):
                raise ValueError("ZIP file path conflicts with a child entry.")
    return validated


def _detect_frameworks(root: Path, inventory: list[dict[str, Any]]) -> list[str]:
    detected: set[str] = set()
    for entry in inventory:
        if Path(entry["path"]).name.lower() != "package.json" or entry["size_bytes"] > 256 * 1024:
            continue
        try:
            raw = (root / entry["path"]).read_bytes()
            package = json.loads(raw.decode("utf-8-sig", errors="replace"))
        except (OSError, json.JSONDecodeError, RecursionError):
            continue
        if isinstance(package, dict):
            deps: dict[str, Any] = {}
            for group in ("dependencies", "devDependencies", "peerDependencies"):
                if isinstance(package.get(group), dict):
                    deps.update(package[group])
            mappings = {
                "react": "React", "next": "Next.js", "vue": "Vue",
                "nuxt": "Nuxt", "@angular/core": "Angular",
                "@sveltejs/kit": "SvelteKit", "svelte": "Svelte",
                "express": "Express", "fastify": "Fastify", "koa": "Koa",
                "vite": "Vite", "electron": "Electron",
            }
            detected.update(label for dependency, label in mappings.items() if dependency in deps)
    file_names = {entry["path"].lower() for entry in inventory}
    root_names = {Path(entry["path"]).name.lower() for entry in inventory}
    if "manage.py" in root_names:
        detected.add("Django")
    if "requirements.txt" in root_names or "pyproject.toml" in root_names:
        for entry in inventory:
            if Path(entry["path"]).name.lower() not in {"requirements.txt", "pyproject.toml"}:
                continue
            try:
                raw = (root / entry["path"]).read_bytes()[:100_000]
                content = raw.decode("utf-8-sig", errors="replace").lower()
            except OSError:
                continue
            for dependency, label in (("fastapi", "FastAPI"), ("flask", "Flask"), ("django", "Django")):
                if dependency in content:
                    detected.add(label)
    if any(path.startswith("src/main/java/") or "/src/main/java/" in path for path in file_names):
        detected.add("Java")
    for marker, label in (("pom.xml", "Spring"), ("angular.json", "Angular"), ("next.config.", "Next.js")):
        if any(Path(path).name.startswith(marker) for path in file_names):
            detected.add(label)
    return sorted(detected)


def _static_source_analysis(root: Path, inventory: list[dict[str, Any]]) -> dict[str, Any]:
    routes: list[dict[str, Any]] = []
    components: list[dict[str, Any]] = []
    integrations: list[dict[str, Any]] = []
    scanned_files = 0
    scanned_bytes = 0
    skipped_files = 0
    for item in inventory:
        path = item["path"]
        suffix = Path(path).suffix.lower()
        if suffix not in SOURCE_EXTENSIONS:
            continue
        if scanned_files >= MAX_SOURCE_FILES or scanned_bytes >= MAX_SOURCE_TOTAL_BYTES:
            skipped_files += 1
            continue
        try:
            with (root / path).open("rb") as stream:
                raw = stream.read(min(MAX_SOURCE_FILE_BYTES, MAX_SOURCE_TOTAL_BYTES - scanned_bytes) + 1)
        except OSError:
            skipped_files += 1
            continue
        if len(raw) > MAX_SOURCE_FILE_BYTES or len(raw) > MAX_SOURCE_TOTAL_BYTES - scanned_bytes:
            raw = raw[:min(MAX_SOURCE_FILE_BYTES, MAX_SOURCE_TOTAL_BYTES - scanned_bytes)]
            skipped_files += 1
        scanned_files += 1
        scanned_bytes += len(raw)
        text = raw.decode("utf-8", errors="replace")
        for line_number, line in enumerate(text.splitlines(), 1):
            for pattern in ROUTE_PATTERNS:
                for match in pattern.finditer(line):
                    route = match.group(1).strip()[:300]
                    if route and len(routes) < MAX_FINDINGS:
                        routes.append({"file": path, "line": line_number, "route": route})
            if suffix in {".jsx", ".tsx", ".js", ".ts", ".vue", ".svelte"}:
                for match in COMPONENT_PATTERN.finditer(line):
                    name = match.group(1) or match.group(2)
                    if name and len(components) < MAX_FINDINGS:
                        components.append({"file": path, "line": line_number, "name": name})
            for pattern in API_PATTERNS:
                match = pattern.search(line)
                if match and len(integrations) < MAX_FINDINGS:
                    integrations.append({
                        "file": path,
                        "line": line_number,
                        "kind": match.group(0)[:120],
                    })
                    break
    return {
        "scanned_source_files": scanned_files,
        "scanned_source_bytes": scanned_bytes,
        "scan_truncated": skipped_files > 0,
        "skipped_source_files": skipped_files,
        "routes": routes,
        "route_count": len(routes),
        "components": components,
        "component_count": len(components),
        "api_integrations": integrations,
        "api_integration_count": len(integrations),
        "analysis_note": "Static text-pattern observations only; results may be incomplete and are not runtime behavior.",
    }


def source_code_excerpts(root: Path, inventory: list[dict[str, Any]]) -> list[dict[str, str]]:
    """Read bounded, text-only excerpts from already preflighted source inventory."""
    excerpts: list[dict[str, str]] = []
    total_bytes = 0
    for item in inventory:
        if Path(item["path"]).suffix.lower() not in SOURCE_EXTENSIONS:
            continue
        if len(excerpts) >= 80 or total_bytes >= 300_000:
            break
        candidate = root / item["path"]
        try:
            resolved = candidate.resolve(strict=True)
            resolved.relative_to(root.resolve(strict=True))
            if candidate.is_symlink() or not resolved.is_file():
                continue
            remaining = min(8_000, 300_000 - total_bytes)
            with resolved.open("rb") as source:
                raw = source.read(remaining + 1)
        except (OSError, ValueError):
            continue
        if len(raw) > remaining:
            raw = raw[:remaining]
        content = "".join(
            character for character in raw.decode("utf-8", errors="replace")
            if character in "\r\n\t" or character.isprintable()
        ).strip()
        if not content:
            continue
        encoded = content.encode("utf-8")
        if len(encoded) > remaining:
            content = encoded[:remaining].decode("utf-8", errors="ignore").strip()
            encoded = content.encode("utf-8")
        if not content:
            continue
        total_bytes += len(encoded)
        excerpts.append({"path": item["path"], "content": content})
    return excerpts


def _inventory(root: Path) -> list[dict[str, Any]]:
    result: list[dict[str, Any]] = []
    for current, directories, files in os.walk(root, followlinks=False):
        directories[:] = sorted(
            directory for directory in directories
            if not (Path(current) / directory).is_symlink()
        )
        for filename in sorted(files):
            full_path = Path(current) / filename
            if full_path.is_symlink() or not full_path.is_file():
                continue
            relative = full_path.relative_to(root).as_posix()
            suffix = full_path.suffix.lower()
            result.append({
                "path": relative,
                "size_bytes": full_path.stat().st_size,
                "extension": suffix or "[no extension]",
                "type": TYPE_BY_EXTENSION.get(suffix, "Other"),
            })
    result.sort(key=lambda entry: entry["path"].casefold())
    return result


def analyze_application_zip(data: bytes, filename: str, destination: Path) -> dict[str, Any]:
    """Validate, extract and statically inspect a ZIP without executing its contents."""
    if not isinstance(filename, str) or Path(filename.replace("\\", "/")).name != filename:
        raise ValueError("Application filename must be a plain file name.")
    if Path(filename).suffix.lower() != ".zip":
        raise ValueError("Only application ZIP files are supported.")
    if not isinstance(data, bytes) or not data:
        raise ValueError("Application ZIP is empty.")
    if len(data) > MAX_ARCHIVE_BYTES:
        raise ValueError("Application ZIP is too large (maximum 25 MB).")

    destination = Path(destination)
    if destination.exists():
        raise ValueError("Application storage destination already exists.")
    destination.parent.mkdir(parents=True, exist_ok=True)
    staging = destination.parent / f".{destination.name}.{uuid4().hex}.tmp"
    try:
        try:
            archive = zipfile.ZipFile(io.BytesIO(data))
        except UnicodeDecodeError as exc:
            raise ValueError(
                "ZIP contains a filename marked as UTF-8 but encoded with invalid bytes. "
                "Recreate the archive with valid UTF-8 filenames."
            ) from exc
        except (zipfile.BadZipFile, OSError, ValueError) as exc:
            raise ValueError("File is not a valid ZIP archive.") from exc
        with archive:
            members = _validated_members(archive)
            staging.mkdir()
            for info, name, is_directory in members:
                target = staging.joinpath(*name.split("/"))
                if is_directory:
                    target.mkdir(parents=True, exist_ok=True)
                    continue
                target.parent.mkdir(parents=True, exist_ok=True)
                resolved_target = target.resolve()
                try:
                    resolved_target.relative_to(staging.resolve())
                except ValueError as exc:
                    raise ValueError("ZIP entry resolves outside the extraction directory.") from exc
                written = 0
                try:
                    source = archive.open(info, "r")
                except (zipfile.BadZipFile, OSError, EOFError, RuntimeError, zlib.error) as exc:
                    raise ValueError("ZIP archive contains a corrupt or unreadable entry.") from exc
                with source, target.open("xb") as output:
                    while True:
                        try:
                            chunk = source.read(64 * 1024)
                        except (zipfile.BadZipFile, OSError, EOFError, RuntimeError, zlib.error) as exc:
                            raise ValueError("ZIP archive contains a corrupt or unreadable entry.") from exc
                        if not chunk:
                            break
                        written += len(chunk)
                        if written > info.file_size or written > MAX_FILE_UNCOMPRESSED:
                            raise ValueError("ZIP entry expanded beyond its declared size.")
                        output.write(chunk)
                if written != info.file_size:
                    raise ValueError("ZIP entry size did not match its directory metadata.")
        inventory = _inventory(staging)
        if len(inventory) > MAX_ENTRIES:
            raise ValueError("Extracted inventory exceeds the entry safety limit.")
        types = Counter(entry["type"] for entry in inventory)
        source_count = sum(Path(entry["path"]).suffix.lower() in SOURCE_EXTENSIONS for entry in inventory)
        config_files = [
            entry["path"] for entry in inventory
            if Path(entry["path"]).name.lower() in CONFIG_NAMES
            or Path(entry["path"]).name.lower().endswith((".config.js", ".config.ts"))
        ]
        analysis = {
            "frameworks": _detect_frameworks(staging, inventory),
            "file_type_counts": dict(sorted(types.items())),
            "total_files": len(inventory),
            "source_file_count": source_count,
            "configuration_files": config_files[:200],
            **_static_source_analysis(staging, inventory),
        }
        destination.parent.mkdir(parents=True, exist_ok=True)
        staging.replace(destination)
        return {
            "filename": Path(filename).name,
            "archive_size_bytes": len(data),
            "inventory": inventory,
            "analysis": analysis,
        }
    except Exception:
        shutil.rmtree(staging, ignore_errors=True)
        raise
