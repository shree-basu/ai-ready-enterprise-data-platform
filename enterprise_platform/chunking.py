"""Deterministic paragraph-aware chunking with durable knowledge identities."""

from __future__ import annotations

import hashlib
import re
from dataclasses import dataclass
from typing import Any


@dataclass(frozen=True)
class ChunkingConfig:
    max_chars: int = 600
    overlap_chars: int = 80
    min_chars: int = 40

    def __post_init__(self) -> None:
        if self.max_chars < 1:
            raise ValueError("max_chars must be positive")
        if not 0 <= self.overlap_chars < self.max_chars:
            raise ValueError("overlap_chars must be between zero and max_chars")
        if not 1 <= self.min_chars <= self.max_chars:
            raise ValueError("min_chars must be between one and max_chars")

    @property
    def version(self) -> str:
        return (
            f"paragraph-v1:max={self.max_chars}:overlap={self.overlap_chars}:min={self.min_chars}"
        )


def _sha256(value: str) -> str:
    return hashlib.sha256(value.encode("utf-8")).hexdigest()


def _split_long_paragraph(paragraph: str, max_chars: int) -> list[str]:
    words = paragraph.split()
    if not words:
        return []
    units: list[str] = []
    current = ""
    for word in words:
        if len(word) > max_chars:
            if current:
                units.append(current)
                current = ""
            units.extend(
                word[offset : offset + max_chars] for offset in range(0, len(word), max_chars)
            )
            continue
        if not current:
            current = word
        elif len(current) + 1 + len(word) <= max_chars:
            current = f"{current} {word}"
        else:
            units.append(current)
            current = word
    if current:
        units.append(current)
    return units


def _paragraph_units(content: str, max_chars: int) -> list[str]:
    paragraphs = [part.strip() for part in re.split(r"\n\s*\n", content) if part.strip()]
    units: list[str] = []
    for paragraph in paragraphs:
        if len(paragraph) <= max_chars:
            units.append(paragraph)
        else:
            units.extend(_split_long_paragraph(paragraph, max_chars))
    return units


def _overlap_suffix(text: str, overlap_chars: int) -> str:
    if overlap_chars == 0:
        return ""
    suffix = text[-overlap_chars:]
    if len(text) > overlap_chars and " " in suffix:
        suffix = suffix.split(" ", 1)[1]
    return suffix.strip()


def chunk_text(content: str, config: ChunkingConfig) -> list[str]:
    """Pack paragraphs deterministically and apply word-aligned overlap."""

    units = _paragraph_units(content.strip(), config.max_chars)
    if not units:
        return []
    chunks: list[str] = []
    current = units[0]
    for unit in units[1:]:
        separator = "\n\n"
        if len(current) + len(separator) + len(unit) <= config.max_chars:
            current = f"{current}{separator}{unit}"
            continue
        chunks.append(current)
        overlap = _overlap_suffix(current, config.overlap_chars)
        candidate = f"{overlap}\n\n{unit}" if overlap else unit
        current = candidate if len(candidate) <= config.max_chars else unit
    chunks.append(current)

    if len(chunks) > 1 and len(chunks[-1]) < config.min_chars:
        combined = f"{chunks[-2]}\n\n{chunks[-1]}"
        if len(combined) <= config.max_chars:
            chunks[-2:] = [combined]
    return chunks


def chunk_document(
    document: dict[str, Any], config: ChunkingConfig | None = None
) -> list[dict[str, Any]]:
    """Create version-aware chunks while preserving authorization and provenance metadata."""

    config = config or ChunkingConfig()
    texts = chunk_text(document["content"], config)
    chunks: list[dict[str, Any]] = []
    for index, text in enumerate(texts):
        text_hash = _sha256(text)
        identity = "\x1f".join(
            (
                document["document_id"],
                str(document["document_version"]),
                document["content_hash"],
                str(index),
                text_hash,
            )
        )
        chunks.append(
            {
                "chunk_id": _sha256(identity),
                "document_id": document["document_id"],
                "document_version": document["document_version"],
                "chunk_index": index,
                "chunk_text": text,
                "chunk_text_hash": text_hash,
                "content_hash": document["content_hash"],
                "classification": document["classification"],
                "allowed_groups": list(document["allowed_groups"]),
                "account_id": document["account_id"],
                "effective_from": document["effective_from"],
                "effective_to": document["effective_to"],
                "source_uri": document["source_uri"],
                "owner": document["owner"],
                "title": document["title"],
                "is_active": document.get("is_active", True),
                "chunk_version": config.version,
            }
        )
    return chunks
