from __future__ import annotations

import json
from pathlib import Path

from evaluation.offline import run_offline_evaluation


def test_offline_evaluation_uses_real_generated_chunks_and_persists_report(
    tmp_path: Path,
) -> None:
    output = tmp_path / "report.json"
    report = run_offline_evaluation(tmp_path / "work", output)
    payload = json.loads(output.read_text(encoding="utf-8"))

    assert report.restricted_refusal_passed is True
    assert set(report.retrieval.metrics_by_mode) == {"semantic", "lexical", "hybrid"}
    assert all(metric.evaluated_cases == 3 for metric in report.retrieval.metrics_by_mode.values())
    assert payload["dataset_version"] == 1
    assert payload["generated_from_scenario"] == "unauthorized-document"
    assert "production latency" in " ".join(payload["limitations"])
