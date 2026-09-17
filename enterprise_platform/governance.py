"""Deterministic authorization outside the model and retrieval ranking layers."""

from __future__ import annotations

from dataclasses import dataclass
from typing import Any

from enterprise_platform.contracts import Classification
from enterprise_platform.embedding_lineage import EmbeddingSpaceMismatch
from enterprise_platform.embeddings import EmbeddingProvider
from enterprise_platform.retrieval import LocalRetriever, RetrievalMode


@dataclass(frozen=True)
class PrincipalContext:
    user_id: str
    groups: frozenset[str]
    region: str

    def __post_init__(self) -> None:
        if not self.user_id.strip():
            raise ValueError("user_id is required")
        if any(not group.strip() for group in self.groups):
            raise ValueError("principal groups cannot contain blank values")
        if not self.region.strip():
            raise ValueError("principal region is required")


@dataclass(frozen=True)
class AccessDecision:
    allowed: bool
    reason: str


@dataclass(frozen=True)
class GovernedEvidence:
    document_id: str
    document_version: int
    chunk_id: str
    citation_id: str
    title: str
    content_kind: str
    content: str
    content_hash: str
    source_uri: str
    classification: str
    retrieval_method: str
    score: float
    semantic_rank: int | None
    lexical_rank: int | None


@dataclass(frozen=True)
class GovernedRetrievalResult:
    evidence: tuple[GovernedEvidence, ...]
    access_denied_candidates: int
    empty_reason: str | None


def authorize_chunk(principal: PrincipalContext, chunk: dict[str, Any]) -> AccessDecision:
    """Fail closed on malformed metadata and enforce region before group access."""

    if not chunk.get("is_active", True):
        return AccessDecision(False, "INACTIVE_DOCUMENT_VERSION")
    classification = chunk.get("classification")
    if classification not in {value.value for value in Classification}:
        return AccessDecision(False, "INVALID_CLASSIFICATION")

    chunk_region = chunk.get("region")
    if chunk_region not in {None, "", "GLOBAL", principal.region}:
        return AccessDecision(False, "REGION_MISMATCH")
    if classification == Classification.PUBLIC:
        return AccessDecision(True, "PUBLIC_DOCUMENT")

    allowed_groups = chunk.get("allowed_groups")
    if not isinstance(allowed_groups, list) or any(
        not isinstance(group, str) for group in allowed_groups
    ):
        return AccessDecision(False, "INVALID_ACCESS_METADATA")
    if principal.groups.intersection(allowed_groups):
        return AccessDecision(True, "AUTHORIZED_GROUP")
    return AccessDecision(False, "GROUP_ACCESS_DENIED")


class GovernedRetriever:
    """Apply identity-based authorization before local candidate scoring."""

    def __init__(self, retriever: LocalRetriever, embedding_provider: EmbeddingProvider) -> None:
        expected_space = (
            embedding_provider.provider_name,
            embedding_provider.model_id,
            embedding_provider.dimension,
            embedding_provider.embedding_version,
        )
        indexed_space = retriever.vector_index.embedding_space
        if indexed_space is not None and indexed_space != expected_space:
            raise EmbeddingSpaceMismatch(
                "query provider does not match the indexed embedding space"
            )
        self.retriever = retriever
        self.embedding_provider = embedding_provider

    def search(
        self,
        query: str,
        *,
        principal: PrincipalContext,
        mode: RetrievalMode = RetrievalMode.HYBRID,
        k: int = 5,
        candidate_k: int = 20,
        account_id: str | None = None,
    ) -> GovernedRetrievalResult:
        query_vectors = self.embedding_provider.embed([query], task_type="RETRIEVAL_QUERY")
        if len(query_vectors) != 1:
            raise ValueError("query embedding provider must return exactly one vector")

        def permitted(chunk: dict[str, Any]) -> bool:
            authorized = authorize_chunk(principal, chunk).allowed
            in_scope = account_id is None or chunk.get("account_id") in {None, account_id}
            return authorized and in_scope

        denied = sum(
            not authorize_chunk(principal, chunk).allowed
            for chunk in self.retriever.vector_index.rows
        )
        hits = self.retriever.search(
            query,
            query_vector=query_vectors[0],
            mode=mode,
            k=k,
            candidate_k=candidate_k,
            metadata_filter=permitted,
        )
        evidence = tuple(
            GovernedEvidence(
                document_id=str(hit.chunk["document_id"]),
                document_version=int(hit.chunk["document_version"]),
                chunk_id=str(hit.chunk["chunk_id"]),
                citation_id=hit.citation_id,
                title=str(hit.chunk["title"]),
                content_kind="UNTRUSTED_DOCUMENT_DATA",
                content=str(hit.chunk["chunk_text"]),
                content_hash=str(hit.chunk["content_hash"]),
                source_uri=str(hit.chunk["source_uri"]),
                classification=str(hit.chunk["classification"]),
                retrieval_method=mode.value,
                score=hit.score,
                semantic_rank=hit.semantic_rank,
                lexical_rank=hit.lexical_rank,
            )
            for hit in hits
        )
        return GovernedRetrievalResult(
            evidence=evidence,
            access_denied_candidates=denied,
            empty_reason=None if evidence else "NO_AUTHORIZED_MATCH",
        )
