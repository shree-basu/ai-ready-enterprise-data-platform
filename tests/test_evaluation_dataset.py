from __future__ import annotations

import json
from pathlib import Path

import pytest

from evaluation.dataset import load_evaluation_dataset

DATASET = Path("evaluation/datasets/enterprise_agent_v1.json")


def test_versioned_dataset_covers_positive_negative_and_operational_cases() -> None:
    dataset = load_evaluation_dataset(DATASET)

    assert dataset.dataset_id == "enterprise-agent-offline-eval"
    assert dataset.version == 1
    assert len(dataset.cases) == 7
    assert {case.expected_access for case in dataset.cases} == {
        "ALLOW",
        "REFUSE",
        "NOT_APPLICABLE",
    }
    assert {tool for case in dataset.cases for tool in case.expected_tools} == {
        "search_enterprise_knowledge",
        "run_governed_sql",
        "get_data_freshness",
        "request_pipeline_reprocessing",
    }


def test_dataset_rejects_duplicate_case_identity(tmp_path: Path) -> None:
    payload = json.loads(DATASET.read_text(encoding="utf-8"))
    payload["cases"].append(dict(payload["cases"][0]))
    invalid = tmp_path / "invalid.json"
    invalid.write_text(json.dumps(payload), encoding="utf-8")

    with pytest.raises(ValueError, match="case IDs must be unique"):
        load_evaluation_dataset(invalid)


def test_dataset_rejects_unversioned_or_unknown_access_contract(tmp_path: Path) -> None:
    payload = json.loads(DATASET.read_text(encoding="utf-8"))
    payload["version"] = 0
    invalid = tmp_path / "invalid.json"
    invalid.write_text(json.dumps(payload), encoding="utf-8")
    with pytest.raises(ValueError, match="positive integer"):
        load_evaluation_dataset(invalid)

    payload["version"] = 1
    payload["cases"][0]["expected_access"] = "MAYBE"
    invalid.write_text(json.dumps(payload), encoding="utf-8")
    with pytest.raises(ValueError, match="unsupported value"):
        load_evaluation_dataset(invalid)
