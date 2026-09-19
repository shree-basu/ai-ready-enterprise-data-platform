"""Deterministic cloud-free BM25-style lexical retrieval."""

from __future__ import annotations

import math
import re
from collections import Counter
from collections.abc import Callable, Iterable
from dataclasses import dataclass
from typing import Any


@dataclass(frozen=True)
class LexicalHit:
    chunk: dict[str, Any]
    score: float


@dataclass(frozen=True)
class LexicalIndexUpdate:
    inserted: int
    replayed: int
    ignored_inactive: int


MetadataFilter = Callable[[dict[str, Any]], bool]

_STOP_WORDS = frozenset(
    {"a", "an", "and", "are", "for", "in", "is", "of", "on", "the", "to", "what", "which"}
)


def tokenize(text: str) -> list[str]:
    """Use a stable, intentionally simple token contract for offline evidence."""

    return [
        token for token in re.findall(r"[a-z0-9]+", text.casefold()) if token not in _STOP_WORDS
    ]


class LocalLexicalIndex:
    """Small deterministic BM25 implementation for local hybrid-search tests."""

    def __init__(
        self,
        rows: Iterable[dict[str, Any]] = (),
        *,
        k1: float = 1.2,
        length_normalization: float = 0.75,
    ) -> None:
        if k1 <= 0:
            raise ValueError("k1 must be positive")
        if not 0 <= length_normalization <= 1:
            raise ValueError("length_normalization must be between zero and one")
        self.k1 = k1
        self.length_normalization = length_normalization
        self._rows: dict[str, dict[str, Any]] = {}
        self.add(rows)

    def add(self, rows: Iterable[dict[str, Any]]) -> LexicalIndexUpdate:
        incoming = [dict(row) for row in rows]
        active = [row for row in incoming if row.get("is_active", True)]
        ignored = len(incoming) - len(active)
        staged = dict(self._rows)
        inserted = 0
        replayed = 0
        for row in active:
            chunk_id = str(row["chunk_id"])
            if not isinstance(row.get("chunk_text"), str):
                raise ValueError(f"chunk_text is required for chunk_id={chunk_id}")
            existing = staged.get(chunk_id)
            if existing is None:
                staged[chunk_id] = row
                inserted += 1
            elif existing == row:
                replayed += 1
            else:
                raise ValueError(f"conflicting active text for chunk_id={chunk_id}")
        self._rows = staged
        return LexicalIndexUpdate(
            inserted=inserted,
            replayed=replayed,
            ignored_inactive=ignored,
        )

    @property
    def size(self) -> int:
        return len(self._rows)

    def search(
        self,
        query: str,
        *,
        k: int = 5,
        metadata_filter: MetadataFilter | None = None,
    ) -> list[LexicalHit]:
        if k < 1:
            raise ValueError("k must be positive")
        query_terms = tokenize(query)
        if not query_terms:
            return []

        candidates = [
            row
            for _, row in sorted(self._rows.items())
            if metadata_filter is None or metadata_filter(row)
        ]
        if not candidates:
            return []
        tokenized = {
            str(row["chunk_id"]): tokenize(f"{row.get('title', '')} {row['chunk_text']}")
            for row in candidates
        }
        average_length = sum(len(tokens) for tokens in tokenized.values()) / len(tokenized)
        if average_length == 0:
            return []
        document_frequency = {
            term: sum(term in set(tokens) for tokens in tokenized.values())
            for term in set(query_terms)
        }

        hits: list[LexicalHit] = []
        for row in candidates:
            tokens = tokenized[str(row["chunk_id"])]
            frequencies = Counter(tokens)
            score = 0.0
            for term, query_frequency in Counter(query_terms).items():
                frequency = frequencies[term]
                if frequency == 0:
                    continue
                frequency_in_documents = document_frequency[term]
                inverse_document_frequency = math.log(
                    1
                    + (len(candidates) - frequency_in_documents + 0.5)
                    / (frequency_in_documents + 0.5)
                )
                denominator = frequency + self.k1 * (
                    1
                    - self.length_normalization
                    + self.length_normalization * len(tokens) / average_length
                )
                score += (
                    query_frequency
                    * inverse_document_frequency
                    * frequency
                    * (self.k1 + 1)
                    / denominator
                )
            if score > 0:
                hits.append(LexicalHit(chunk=dict(row), score=score))
        return sorted(hits, key=lambda hit: (-hit.score, hit.chunk["chunk_id"]))[:k]
