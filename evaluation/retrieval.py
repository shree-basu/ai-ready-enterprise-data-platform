"""Deterministic offline retrieval metrics with no external evaluator."""

from __future__ import annotations

import json
import math
from dataclasses import asdict, dataclass
from pathlib import Path

from enterprise_platform.retrieval import RetrievalMode
from evaluation.dataset import EvaluationDataset


@dataclass(frozen=True)
class RetrievalJudgment:
    case_id: str
    relevant_chunk_ids: frozenset[str]


@dataclass(frozen=True)
class RankedResult:
    case_id: str
    ranked_chunk_ids: tuple[str, ...]


@dataclass(frozen=True)
class RetrievalMetrics:
    evaluated_cases: int
    k: int
    recall_at_k: float
    mean_reciprocal_rank: float
    ndcg_at_k: float
    empty_result_rate: float


@dataclass(frozen=True)
class RetrievalComparison:
    dataset_id: str
    dataset_version: int
    metrics_by_mode: dict[str, RetrievalMetrics]


def judgments_from_dataset(dataset: EvaluationDataset) -> tuple[RetrievalJudgment, ...]:
    """Select only cases with positive retrieval relevance labels."""

    return tuple(
        RetrievalJudgment(case.case_id, frozenset(case.relevant_chunk_ids))
        for case in dataset.cases
        if case.relevant_chunk_ids
    )


def _validate_inputs(
    judgments: tuple[RetrievalJudgment, ...],
    results: tuple[RankedResult, ...],
    k: int,
) -> dict[str, RankedResult]:
    if k < 1:
        raise ValueError("k must be positive")
    if not judgments:
        raise ValueError("at least one retrieval judgment is required")
    judgment_ids = [item.case_id for item in judgments]
    if len(judgment_ids) != len(set(judgment_ids)):
        raise ValueError("retrieval judgment case IDs must be unique")
    if any(not item.relevant_chunk_ids for item in judgments):
        raise ValueError("every retrieval judgment requires a relevant chunk")
    result_ids = [item.case_id for item in results]
    if len(result_ids) != len(set(result_ids)):
        raise ValueError("ranked result case IDs must be unique")
    by_case = {item.case_id: item for item in results}
    if set(by_case) != set(judgment_ids):
        raise ValueError("ranked results must cover exactly the judged cases")
    if any(len(item.ranked_chunk_ids) != len(set(item.ranked_chunk_ids)) for item in results):
        raise ValueError("ranked results cannot contain duplicate chunk IDs")
    return by_case


def evaluate_retrieval(
    judgments: tuple[RetrievalJudgment, ...],
    results: tuple[RankedResult, ...],
    *,
    k: int,
) -> RetrievalMetrics:
    """Compute macro Recall@K, MRR, nDCG@K, and empty-result rate."""

    by_case = _validate_inputs(judgments, results, k)
    recalls: list[float] = []
    reciprocal_ranks: list[float] = []
    ndcgs: list[float] = []
    empty = 0
    for judgment in judgments:
        ranked = by_case[judgment.case_id].ranked_chunk_ids[:k]
        if not ranked:
            empty += 1
        relevant_ranks = [
            rank
            for rank, chunk_id in enumerate(ranked, start=1)
            if chunk_id in judgment.relevant_chunk_ids
        ]
        recalls.append(len(relevant_ranks) / len(judgment.relevant_chunk_ids))
        reciprocal_ranks.append(0.0 if not relevant_ranks else 1.0 / min(relevant_ranks))
        dcg = sum(1.0 / math.log2(rank + 1) for rank in relevant_ranks)
        ideal_count = min(len(judgment.relevant_chunk_ids), k)
        ideal_dcg = sum(1.0 / math.log2(rank + 1) for rank in range(1, ideal_count + 1))
        ndcgs.append(dcg / ideal_dcg)

    count = len(judgments)
    return RetrievalMetrics(
        evaluated_cases=count,
        k=k,
        recall_at_k=sum(recalls) / count,
        mean_reciprocal_rank=sum(reciprocal_ranks) / count,
        ndcg_at_k=sum(ndcgs) / count,
        empty_result_rate=empty / count,
    )


def compare_retrieval_modes(
    dataset: EvaluationDataset,
    results_by_mode: dict[RetrievalMode, tuple[RankedResult, ...]],
    *,
    k: int,
) -> RetrievalComparison:
    """Evaluate every declared mode over the same positive cases."""

    required = set(RetrievalMode)
    if set(results_by_mode) != required:
        raise ValueError("semantic, lexical, and hybrid results are all required")
    judgments = judgments_from_dataset(dataset)
    return RetrievalComparison(
        dataset_id=dataset.dataset_id,
        dataset_version=dataset.version,
        metrics_by_mode={
            mode.value: evaluate_retrieval(judgments, results_by_mode[mode], k=k)
            for mode in RetrievalMode
        },
    )


def write_retrieval_report(report: RetrievalComparison, path: Path) -> Path:
    """Persist a stable local report without claiming online or human evaluation."""

    path.parent.mkdir(parents=True, exist_ok=True)
    payload = json.dumps(asdict(report), sort_keys=True, indent=2, allow_nan=False) + "\n"
    staging = path.with_suffix(path.suffix + ".tmp")
    staging.write_text(payload, encoding="utf-8", newline="\n")
    staging.replace(path)
    return path
