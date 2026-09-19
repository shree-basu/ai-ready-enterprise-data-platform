from __future__ import annotations

import json
from pathlib import Path

import pytest

from enterprise_platform.retrieval import RetrievalMode
from evaluation.dataset import load_evaluation_dataset
from evaluation.retrieval import (
    RankedResult,
    RetrievalJudgment,
    compare_retrieval_modes,
    evaluate_retrieval,
    write_retrieval_report,
)


def test_recall_mrr_ndcg_and_empty_rate_are_computed_without_cherry_picking() -> None:
    judgments = (
        RetrievalJudgment("one", frozenset({"a"})),
        RetrievalJudgment("two", frozenset({"c", "d"})),
        RetrievalJudgment("three", frozenset({"e"})),
    )
    results = (
        RankedResult("one", ("b", "a")),
        RankedResult("two", ("c", "x")),
        RankedResult("three", ()),
    )

    metrics = evaluate_retrieval(judgments, results, k=2)

    assert metrics.evaluated_cases == 3
    assert metrics.recall_at_k == pytest.approx(0.5)
    assert metrics.mean_reciprocal_rank == pytest.approx(0.5)
    assert metrics.ndcg_at_k == pytest.approx((1 / 1.5849625007 + 1 / 1.6309297536) / 3)
    assert metrics.empty_result_rate == pytest.approx(1 / 3)


def test_all_modes_use_the_same_versioned_cases_and_report_is_stable(tmp_path: Path) -> None:
    dataset = load_evaluation_dataset(Path("evaluation/datasets/enterprise_agent_v1.json"))
    result = (
        RankedResult(
            "knowledge-authorized-runbook",
            ("d1891a879edf9ed821f6273c6d5cb985d112a86d0b51832258cb7dd0cd426c81",),
        ),
        RankedResult(
            "knowledge-account-incident",
            ("d9179060391aab4f76af904687700c222b157ea613fbe08906aa56e884d6437a",),
        ),
        RankedResult(
            "knowledge-finance-policy",
            ("b3dcbe3c28d4fbf190234759ed62f1da1628d7e6fdc18abfa939a6293cdca237",),
        ),
    )
    comparison = compare_retrieval_modes(
        dataset,
        {mode: result for mode in RetrievalMode},
        k=3,
    )

    first = write_retrieval_report(comparison, tmp_path / "report.json").read_bytes()
    second = write_retrieval_report(comparison, tmp_path / "report.json").read_bytes()
    payload = json.loads(first)

    assert first == second
    assert set(payload["metrics_by_mode"]) == {"semantic", "lexical", "hybrid"}
    assert all(item["recall_at_k"] == 1.0 for item in payload["metrics_by_mode"].values())


def test_evaluation_rejects_missing_cases_duplicates_and_partial_mode_comparison() -> None:
    judgment = (RetrievalJudgment("case", frozenset({"a"})),)
    with pytest.raises(ValueError, match="cover exactly"):
        evaluate_retrieval(judgment, (), k=1)
    with pytest.raises(ValueError, match="duplicate chunk IDs"):
        evaluate_retrieval(judgment, (RankedResult("case", ("a", "a")),), k=2)

    dataset = load_evaluation_dataset(Path("evaluation/datasets/enterprise_agent_v1.json"))
    with pytest.raises(ValueError, match="all required"):
        compare_retrieval_modes(dataset, {}, k=3)
