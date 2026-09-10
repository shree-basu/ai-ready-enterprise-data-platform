"""Deterministic natural-key deduplication and structured reference checks."""

from __future__ import annotations

import hashlib
import json
from collections import defaultdict
from collections.abc import Iterable
from datetime import date, datetime
from decimal import Decimal
from typing import Any

from enterprise_platform.quality import ContractViolation, natural_key


def _json_value(value: Any) -> Any:
    if isinstance(value, datetime):
        return value.isoformat()
    if isinstance(value, date):
        return value.isoformat()
    if isinstance(value, Decimal):
        return format(value, "f")
    return value


def canonical_record_hash(row: dict[str, Any]) -> str:
    """Create a stable tie-breaker without depending on input order."""

    payload = {key: _json_value(value) for key, value in sorted(row.items())}
    encoded = json.dumps(payload, sort_keys=True, separators=(",", ":")).encode()
    return hashlib.sha256(encoded).hexdigest()


def deterministic_deduplicate(
    entity: str, rows: Iterable[dict[str, Any]]
) -> tuple[list[dict[str, Any]], list[ContractViolation]]:
    """Keep latest updated_at, then greatest canonical hash; quarantine every loser."""

    groups: dict[tuple[Any, ...], list[dict[str, Any]]] = defaultdict(list)
    for row in rows:
        groups[natural_key(entity, row)].append(row)

    accepted: list[dict[str, Any]] = []
    quarantined: list[ContractViolation] = []
    for key in sorted(groups, key=lambda value: tuple(str(part) for part in value)):
        candidates = sorted(
            groups[key],
            key=lambda row: (row["updated_at"], canonical_record_hash(row)),
            reverse=True,
        )
        accepted.append(candidates[0])
        for duplicate in candidates[1:]:
            quarantined.append(
                ContractViolation(
                    entity=entity,
                    code="DUPLICATE_NATURAL_KEY",
                    message=f"superseded deterministic duplicate for natural key {key!r}",
                    raw_record=duplicate,
                )
            )
    return accepted, quarantined


def quarantine_orphan_accounts(
    entity: str,
    rows: Iterable[dict[str, Any]],
    *,
    valid_account_ids: set[str],
) -> tuple[list[dict[str, Any]], list[ContractViolation]]:
    """Reject child records whose account_id is absent from accepted accounts."""

    if entity == "accounts":
        return list(rows), []
    accepted: list[dict[str, Any]] = []
    quarantined: list[ContractViolation] = []
    for row in rows:
        if row["account_id"] in valid_account_ids:
            accepted.append(row)
        else:
            quarantined.append(
                ContractViolation(
                    entity=entity,
                    code="ORPHAN_ACCOUNT_REFERENCE",
                    message=f"account_id {row['account_id']!r} is not accepted for this batch",
                    raw_record=row,
                )
            )
    return accepted, quarantined
