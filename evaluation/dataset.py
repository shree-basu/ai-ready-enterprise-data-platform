"""Versioned offline evaluation dataset contract."""

from __future__ import annotations

import json
from dataclasses import dataclass
from pathlib import Path
from typing import Any


@dataclass(frozen=True)
class EvaluationCase:
    case_id: str
    question: str
    user_id: str
    groups: tuple[str, ...]
    region: str
    account_id: str | None
    expected_tools: tuple[str, ...]
    relevant_document_ids: tuple[str, ...]
    relevant_chunk_ids: tuple[str, ...]
    expected_citation_ids: tuple[str, ...]
    expected_access: str
    freshness_requirement: str
    expected_structured_result: dict[str, Any] | None


@dataclass(frozen=True)
class EvaluationDataset:
    dataset_id: str
    version: int
    cases: tuple[EvaluationCase, ...]


_ACCESS_OUTCOMES = frozenset({"ALLOW", "REFUSE", "NOT_APPLICABLE"})
_FRESHNESS_REQUIREMENTS = frozenset({"FRESH", "WARN_IF_STALE", "NOT_APPLICABLE"})


def _nonempty_string(value: Any, field: str) -> str:
    if not isinstance(value, str) or not value.strip():
        raise ValueError(f"{field} must be a non-empty string")
    return value.strip()


def _string_tuple(value: Any, field: str) -> tuple[str, ...]:
    if not isinstance(value, list) or any(not isinstance(item, str) or not item for item in value):
        raise ValueError(f"{field} must be an array of non-empty strings")
    if len(value) != len(set(value)):
        raise ValueError(f"{field} cannot contain duplicates")
    return tuple(value)


def _case(record: Any) -> EvaluationCase:
    if not isinstance(record, dict):
        raise ValueError("evaluation cases must be objects")
    expected_access = _nonempty_string(record.get("expected_access"), "expected_access")
    if expected_access not in _ACCESS_OUTCOMES:
        raise ValueError("expected_access has an unsupported value")
    freshness = _nonempty_string(record.get("freshness_requirement"), "freshness_requirement")
    if freshness not in _FRESHNESS_REQUIREMENTS:
        raise ValueError("freshness_requirement has an unsupported value")
    structured = record.get("expected_structured_result")
    if structured is not None and not isinstance(structured, dict):
        raise ValueError("expected_structured_result must be an object or null")
    account_id = record.get("account_id")
    if account_id is not None:
        account_id = _nonempty_string(account_id, "account_id")
    return EvaluationCase(
        case_id=_nonempty_string(record.get("case_id"), "case_id"),
        question=_nonempty_string(record.get("question"), "question"),
        user_id=_nonempty_string(record.get("user_id"), "user_id"),
        groups=_string_tuple(record.get("groups"), "groups"),
        region=_nonempty_string(record.get("region"), "region"),
        account_id=account_id,
        expected_tools=_string_tuple(record.get("expected_tools"), "expected_tools"),
        relevant_document_ids=_string_tuple(
            record.get("relevant_document_ids"), "relevant_document_ids"
        ),
        relevant_chunk_ids=_string_tuple(record.get("relevant_chunk_ids"), "relevant_chunk_ids"),
        expected_citation_ids=_string_tuple(
            record.get("expected_citation_ids"), "expected_citation_ids"
        ),
        expected_access=expected_access,
        freshness_requirement=freshness,
        expected_structured_result=structured,
    )


def load_evaluation_dataset(path: Path) -> EvaluationDataset:
    """Load a strict, explicitly versioned JSON evaluation dataset."""

    payload = json.loads(path.read_text(encoding="utf-8"))
    if not isinstance(payload, dict):
        raise ValueError("evaluation dataset must be an object")
    version = payload.get("version")
    if not isinstance(version, int) or isinstance(version, bool) or version < 1:
        raise ValueError("evaluation dataset version must be a positive integer")
    records = payload.get("cases")
    if not isinstance(records, list) or not records:
        raise ValueError("evaluation dataset requires at least one case")
    cases = tuple(_case(record) for record in records)
    case_ids = [case.case_id for case in cases]
    if len(case_ids) != len(set(case_ids)):
        raise ValueError("evaluation case IDs must be unique")
    return EvaluationDataset(
        dataset_id=_nonempty_string(payload.get("dataset_id"), "dataset_id"),
        version=version,
        cases=cases,
    )
