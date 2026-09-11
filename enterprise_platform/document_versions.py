"""Deterministic document-version resolution across replayed immutable batches."""

from __future__ import annotations

import hashlib
from collections.abc import Iterable
from dataclasses import dataclass
from datetime import date
from typing import Any

from enterprise_platform.documents import DocumentViolation


@dataclass(frozen=True)
class DocumentVersionResult:
    documents: list[dict[str, Any]]
    quarantined: list[DocumentViolation]
    replayed_documents: int


def _stable_id(*parts: str) -> str:
    return hashlib.sha256("\x1f".join(parts).encode()).hexdigest()


def _version_identity(document: dict[str, Any]) -> tuple[str, int]:
    return document["document_id"], document["document_version"]


def _decorate(document: dict[str, Any], *, active: bool, as_of_date: date) -> dict[str, Any]:
    document_id, version = _version_identity(document)
    return {
        **document,
        "document_identity": _stable_id(document_id),
        "document_version_id": _stable_id(document_id, str(version)),
        "is_active": active,
        "resolved_as_of": as_of_date,
    }


def _choose_active_versions(
    documents: Iterable[dict[str, Any]], as_of_date: date
) -> dict[str, int]:
    active: dict[str, int] = {}
    for document in documents:
        if document["effective_from"] > as_of_date:
            continue
        if document["effective_to"] is not None and document["effective_to"] < as_of_date:
            continue
        document_id = document["document_id"]
        active[document_id] = max(active.get(document_id, 0), document["document_version"])
    return active


def resolve_document_versions(
    incoming: Iterable[dict[str, Any]],
    *,
    as_of_date: date,
    prior_versions: Iterable[dict[str, Any]] = (),
) -> DocumentVersionResult:
    """Merge valid versions without mutation, regression, or duplicate active state."""

    prior = list(prior_versions)
    prior_by_identity = {_version_identity(document): document for document in prior}
    highest_prior = {
        document_id: max(
            document["document_version"]
            for document in prior
            if document["document_id"] == document_id
        )
        for document_id in {document["document_id"] for document in prior}
    }

    grouped_incoming: dict[tuple[str, int], list[dict[str, Any]]] = {}
    for document in incoming:
        grouped_incoming.setdefault(_version_identity(document), []).append(document)

    accepted_new: list[dict[str, Any]] = []
    quarantined: list[DocumentViolation] = []
    replayed = 0
    for identity in sorted(grouped_incoming):
        candidates = grouped_incoming[identity]
        unique_hashes = {document["content_hash"] for document in candidates}
        if len(unique_hashes) > 1:
            quarantined.extend(
                DocumentViolation(
                    code="DOCUMENT_VERSION_MUTATION",
                    message=(
                        f"one version identity contains conflicting content hashes: {identity!r}"
                    ),
                    raw_record=document,
                )
                for document in candidates
            )
            continue

        candidate = sorted(
            candidates,
            key=lambda document: (document["updated_at"], document["content_hash"]),
            reverse=True,
        )[0]
        replayed += len(candidates) - 1
        if identity in prior_by_identity:
            if prior_by_identity[identity]["content_hash"] == candidate["content_hash"]:
                replayed += 1
            else:
                quarantined.append(
                    DocumentViolation(
                        code="DOCUMENT_VERSION_MUTATION",
                        message=(
                            f"document version content changed without a new version: {identity!r}"
                        ),
                        raw_record=candidate,
                    )
                )
            continue

        document_id, version = identity
        if version < highest_prior.get(document_id, 0):
            quarantined.append(
                DocumentViolation(
                    code="STALE_DOCUMENT_VERSION",
                    message=f"version {version} is older than the current version",
                    raw_record=candidate,
                )
            )
            continue
        accepted_new.append(candidate)

    combined = prior + accepted_new
    active_versions = _choose_active_versions(combined, as_of_date)
    resolved = [
        _decorate(
            document,
            active=active_versions.get(document["document_id"]) == document["document_version"],
            as_of_date=as_of_date,
        )
        for document in sorted(combined, key=_version_identity)
    ]
    return DocumentVersionResult(
        documents=resolved,
        quarantined=quarantined,
        replayed_documents=replayed,
    )
