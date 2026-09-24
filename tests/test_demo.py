from __future__ import annotations

from pathlib import Path

from demo.run import run_demo


def test_complete_local_demo_exercises_every_governed_layer(tmp_path: Path) -> None:
    report = run_demo(tmp_path / "demo")

    assert report.scenario == "normal"
    assert report.structured_accepted_rows == report.publication_inserted_rows == 21
    assert report.structured_quarantined_rows == 0
    assert report.documents_processed == report.chunks_indexed == 5
    assert report.agent_tool_calls == (
        "run_governed_sql",
        "search_enterprise_knowledge",
        "get_data_freshness",
        "request_pipeline_reprocessing",
    )
    assert "DOC-RUNBOOK-001#" in report.agent_answers[1]
    assert report.agent_answers[2].startswith("Freshness status FRESH")
    assert report.operation_status == "PENDING_APPROVAL"
    assert report.audit_events >= 5
    assert report.cloud_operations == ()
