"""Cloud-free structured batch processing and replay-safe local publication."""

from __future__ import annotations

import csv
import hashlib
import json
import shutil
import tempfile
from dataclasses import asdict, dataclass
from datetime import date, datetime
from decimal import Decimal
from pathlib import Path
from typing import Any

from enterprise_platform.contracts import SCHEMA_VERSION, SOURCE_CONTRACTS
from enterprise_platform.dedupe import (
    canonical_record_hash,
    deterministic_deduplicate,
    quarantine_orphan_accounts,
)
from enterprise_platform.manifest import load_and_validate_manifest
from enterprise_platform.quality import ContractViolation, natural_key, validate_structured_row

STRUCTURED_ENTITIES = tuple(name for name in SOURCE_CONTRACTS if name != "documents")


@dataclass(frozen=True)
class Reconciliation:
    entity: str
    source_rows: int
    accepted_rows: int
    quarantined_rows: int

    @property
    def balanced(self) -> bool:
        return self.source_rows == self.accepted_rows + self.quarantined_rows


@dataclass(frozen=True)
class StructuredBatchResult:
    business_date: date
    batch_id: str
    pipeline_run_id: str
    accepted: dict[str, list[dict[str, Any]]]
    quarantined: list[dict[str, Any]]
    reconciliation: dict[str, Reconciliation]


class ReplayConflict(RuntimeError):
    """Raised when an immutable output identity already contains different bytes."""


def _json_value(value: Any) -> Any:
    if isinstance(value, date | datetime):
        return value.isoformat()
    if isinstance(value, Decimal):
        return format(value, "f")
    if isinstance(value, dict):
        return {key: _json_value(item) for key, item in sorted(value.items())}
    if isinstance(value, list | tuple):
        return [_json_value(item) for item in value]
    return value


def _stable_id(*parts: str) -> str:
    return hashlib.sha256("\x1f".join(parts).encode()).hexdigest()


def _lineage_record(
    entity: str, row: dict[str, Any], *, business_date: date, batch_id: str
) -> dict[str, Any]:
    key = natural_key(entity, row)
    record_hash = canonical_record_hash(row)
    return {
        **row,
        "_lineage": {
            "business_date": business_date,
            "batch_id": batch_id,
            "entity": entity,
            "schema_version": SCHEMA_VERSION,
            "natural_key": key,
            "record_hash": record_hash,
            "structured_row_id": _stable_id(batch_id, entity, repr(key), record_hash),
        },
    }


def _quarantine_record(violation: ContractViolation, *, batch_id: str) -> dict[str, Any]:
    raw_hash = canonical_record_hash(violation.raw_record)
    return {
        **violation.as_record(),
        "batch_id": batch_id,
        "raw_record_hash": raw_hash,
        "quarantine_id": _stable_id(batch_id, violation.entity, violation.code, raw_hash),
    }


def process_structured_records(
    raw_by_entity: dict[str, list[dict[str, Any]]],
    *,
    business_date: date,
    batch_id: str,
) -> StructuredBatchResult:
    """Validate, deduplicate, reference-check, reconcile, and attach lineage."""

    if set(raw_by_entity) != set(STRUCTURED_ENTITIES):
        raise ValueError("raw input must contain exactly the structured entities")

    valid: dict[str, list[dict[str, Any]]] = {}
    violations: list[ContractViolation] = []
    for entity in STRUCTURED_ENTITIES:
        valid[entity] = []
        for raw in raw_by_entity[entity]:
            accepted, violation = validate_structured_row(entity, raw, business_date=business_date)
            if accepted is not None:
                valid[entity].append(accepted)
            else:
                assert violation is not None
                violations.append(violation)

    deduplicated: dict[str, list[dict[str, Any]]] = {}
    for entity in STRUCTURED_ENTITIES:
        deduplicated[entity], duplicate_violations = deterministic_deduplicate(
            entity, valid[entity]
        )
        violations.extend(duplicate_violations)

    account_ids = {row["account_id"] for row in deduplicated["accounts"]}
    accepted_rows: dict[str, list[dict[str, Any]]] = {"accounts": deduplicated["accounts"]}
    for entity in STRUCTURED_ENTITIES[1:]:
        accepted_rows[entity], orphan_violations = quarantine_orphan_accounts(
            entity, deduplicated[entity], valid_account_ids=account_ids
        )
        violations.extend(orphan_violations)

    accepted = {
        entity: [
            _lineage_record(entity, row, business_date=business_date, batch_id=batch_id)
            for row in accepted_rows[entity]
        ]
        for entity in STRUCTURED_ENTITIES
    }
    quarantined = [_quarantine_record(violation, batch_id=batch_id) for violation in violations]
    reconciliation = {
        entity: Reconciliation(
            entity=entity,
            source_rows=len(raw_by_entity[entity]),
            accepted_rows=len(accepted[entity]),
            quarantined_rows=sum(1 for row in quarantined if row["entity"] == entity),
        )
        for entity in STRUCTURED_ENTITIES
    }
    if not all(result.balanced for result in reconciliation.values()):
        raise RuntimeError("source, accepted, and quarantine counts do not reconcile")

    return StructuredBatchResult(
        business_date=business_date,
        batch_id=batch_id,
        pipeline_run_id=_stable_id(business_date.isoformat(), batch_id, SCHEMA_VERSION),
        accepted=accepted,
        quarantined=quarantined,
        reconciliation=reconciliation,
    )


def read_structured_batch(batch_dir: Path) -> tuple[dict[str, Any], dict[str, list[dict]]]:
    manifest = load_and_validate_manifest(batch_dir)
    raw: dict[str, list[dict]] = {}
    for entity in STRUCTURED_ENTITIES:
        path = batch_dir / SOURCE_CONTRACTS[entity].relative_path
        with path.open(encoding="utf-8", newline="") as handle:
            raw[entity] = list(csv.DictReader(handle))
    return manifest, raw


def _render_outputs(result: StructuredBatchResult) -> dict[str, bytes]:
    outputs: dict[str, bytes] = {}
    for entity, rows in result.accepted.items():
        text = "".join(
            json.dumps(_json_value(row), sort_keys=True, separators=(",", ":")) + "\n"
            for row in rows
        )
        outputs[f"accepted/{entity}.jsonl"] = text.encode()
    quarantine = "".join(
        json.dumps(_json_value(row), sort_keys=True, separators=(",", ":")) + "\n"
        for row in result.quarantined
    )
    outputs["quarantine/records.jsonl"] = quarantine.encode()
    summary = {
        "business_date": result.business_date,
        "batch_id": result.batch_id,
        "pipeline_run_id": result.pipeline_run_id,
        "entities": {
            entity: asdict(reconciliation)
            for entity, reconciliation in result.reconciliation.items()
        },
    }
    outputs["reconciliation.json"] = (
        json.dumps(_json_value(summary), indent=2, sort_keys=True) + "\n"
    ).encode()
    outputs["_SUCCESS"] = b""
    return outputs


def _existing_outputs(target: Path) -> dict[str, bytes]:
    return {
        path.relative_to(target).as_posix(): path.read_bytes()
        for path in target.rglob("*")
        if path.is_file()
    }


def publish_structured_batch(batch_dir: Path, output_root: Path) -> Path:
    """Publish deterministic local outputs once; an identical replay becomes a no-op."""

    manifest, raw = read_structured_batch(batch_dir)
    business_date = date.fromisoformat(manifest["business_date"])
    result = process_structured_records(
        raw, business_date=business_date, batch_id=manifest["batch_id"]
    )
    outputs = _render_outputs(result)
    target = (
        output_root / f"business_date={business_date.isoformat()}" / f"batch_id={result.batch_id}"
    )
    if target.exists():
        if _existing_outputs(target) == outputs:
            return target
        raise ReplayConflict(f"output identity already exists with different bytes: {target}")

    target.parent.mkdir(parents=True, exist_ok=True)
    staging = Path(tempfile.mkdtemp(prefix=f".{result.batch_id}-", dir=target.parent))
    try:
        for relative_path, payload in outputs.items():
            path = staging / relative_path
            path.parent.mkdir(parents=True, exist_ok=True)
            path.write_bytes(payload)
        staging.rename(target)
    except Exception:
        shutil.rmtree(staging, ignore_errors=True)
        raise
    return target
