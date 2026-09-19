"""Validate immutable local deliveries before any processing begins."""

from __future__ import annotations

import csv
import hashlib
import json
import re
from datetime import date
from pathlib import Path
from typing import Any

from enterprise_platform.contracts import SCHEMA_VERSION, SOURCE_CONTRACTS
from enterprise_platform.schemas import STRUCTURED_SCHEMAS, field_names

_BATCH_ID_PATTERN = re.compile(r"[A-Za-z0-9][A-Za-z0-9._-]{0,63}")
_CONTROL_FILES = frozenset({"manifest.json", "_SUCCESS"})


class ManifestViolation(ValueError):
    """Raised when a delivery is incomplete, redirected, or internally inconsistent."""


def _fail(message: str) -> ManifestViolation:
    return ManifestViolation(message)


def _sha256(path: Path) -> str:
    digest = hashlib.sha256()
    with path.open("rb") as handle:
        for block in iter(lambda: handle.read(1024 * 1024), b""):
            digest.update(block)
    return digest.hexdigest()


def _row_count(path: Path, file_format: str) -> int:
    if file_format == "csv":
        with path.open(encoding="utf-8", newline="") as handle:
            return sum(1 for _ in csv.reader(handle)) - 1
    if file_format == "jsonl":
        with path.open(encoding="utf-8") as handle:
            return sum(1 for _ in handle)
    raise _fail(f"unsupported file format: {file_format}")


def _validate_identity(batch_dir: Path, manifest: dict[str, Any]) -> None:
    try:
        business_date = date.fromisoformat(manifest["business_date"])
        batch_id = manifest["batch_id"]
    except (KeyError, TypeError, ValueError) as exc:
        raise _fail("manifest business_date and batch_id are required and valid") from exc

    if not isinstance(batch_id, str) or not _BATCH_ID_PATTERN.fullmatch(batch_id):
        raise _fail("manifest batch_id is not a safe path segment")
    expected_parts = (
        f"business_date={business_date.isoformat()}",
        f"batch_id={batch_id}",
    )
    if batch_dir.parent.name != expected_parts[0] or batch_dir.name != expected_parts[1]:
        raise _fail("directory identity does not match manifest business_date and batch_id")


def _validate_csv_header(path: Path, entity: str) -> None:
    with path.open(encoding="utf-8", newline="") as handle:
        header = next(csv.reader(handle), None)
    expected = list(field_names(STRUCTURED_SCHEMAS[entity]))
    if header != expected:
        raise _fail(f"{entity} CSV header does not match schema {SCHEMA_VERSION}")


def load_and_validate_manifest(batch_dir: Path) -> dict[str, Any]:
    """Return the manifest only after exact identity, inventory, count, and hash checks."""

    batch_dir = batch_dir.resolve()
    manifest_path = batch_dir / "manifest.json"
    success_path = batch_dir / "_SUCCESS"
    if not manifest_path.is_file():
        raise _fail("manifest.json is required")
    if not success_path.is_file() or success_path.stat().st_size != 0:
        raise _fail("an empty _SUCCESS marker is required")

    try:
        manifest = json.loads(manifest_path.read_text(encoding="utf-8"))
    except (OSError, json.JSONDecodeError) as exc:
        raise _fail("manifest.json must contain valid JSON") from exc
    if not isinstance(manifest, dict):
        raise _fail("manifest root must be an object")
    if manifest.get("schema_version") != SCHEMA_VERSION:
        raise _fail(f"unsupported schema_version; expected {SCHEMA_VERSION}")
    _validate_identity(batch_dir, manifest)

    files = manifest.get("files")
    if not isinstance(files, dict) or set(files) != set(SOURCE_CONTRACTS):
        raise _fail("manifest must contain exactly the contracted entities")

    expected_paths = {contract.relative_path for contract in SOURCE_CONTRACTS.values()}
    actual_paths = {
        path.relative_to(batch_dir).as_posix() for path in batch_dir.rglob("*") if path.is_file()
    }
    if actual_paths != expected_paths | _CONTROL_FILES:
        raise _fail("delivery contains missing or unexpected files")

    for entity, contract in SOURCE_CONTRACTS.items():
        metadata = files[entity]
        if not isinstance(metadata, dict):
            raise _fail(f"manifest entry for {entity} must be an object")
        if metadata.get("path") != contract.relative_path:
            raise _fail(f"manifest path redirect rejected for {entity}")
        if metadata.get("format") != contract.file_format:
            raise _fail(f"manifest format mismatch for {entity}")
        expected_count = metadata.get("expected_row_count")
        if isinstance(expected_count, bool) or not isinstance(expected_count, int):
            raise _fail(f"expected_row_count for {entity} must be an integer")
        if expected_count < 0:
            raise _fail(f"expected_row_count for {entity} cannot be negative")

        path = (batch_dir / contract.relative_path).resolve()
        if not path.is_relative_to(batch_dir) or not path.is_file():
            raise _fail(f"contracted file is unavailable for {entity}")
        if _row_count(path, contract.file_format) != expected_count:
            raise _fail(f"row-count mismatch for {entity}")
        if _sha256(path) != metadata.get("sha256"):
            raise _fail(f"checksum mismatch for {entity}")
        if contract.file_format == "csv":
            _validate_csv_header(path, entity)

    return manifest
