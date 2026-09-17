from __future__ import annotations

from pathlib import Path

import pytest

from evaluation.agent import AgentObservation, evaluate_agent_observations
from evaluation.dataset import load_evaluation_dataset

DATASET = load_evaluation_dataset(Path("evaluation/datasets/enterprise_agent_v1.json"))


def _passing_observations() -> tuple[AgentObservation, ...]:
    return (
        AgentObservation(
            case_id="knowledge-authorized-runbook",
            answer="Use the approved API incident runbook.",
            tool_calls=("search_enterprise_knowledge",),
            citation_ids=("DOC-RUNBOOK#chunk-runbook",),
            evidence_by_citation=(("DOC-RUNBOOK#chunk-runbook", "approved API incident runbook"),),
            authorized_citation_ids=frozenset({"DOC-RUNBOOK#chunk-runbook"}),
            asserted_facts=("approved API incident runbook",),
        ),
        AgentObservation(
            case_id="knowledge-restricted-refusal",
            answer="No authorized knowledge evidence was found.",
            tool_calls=("search_enterprise_knowledge",),
        ),
        AgentObservation(
            case_id="analytics-account-overdue",
            answer="A-1001 has an overdue amount of 2500. WARNING: analytics are stale.",
            tool_calls=("run_governed_sql",),
            data_is_stale=True,
            stale_warning_surfaced=True,
        ),
        AgentObservation(
            case_id="freshness-discrepancy",
            answer="WARNING: analytics freshness is STALE.",
            tool_calls=("get_data_freshness",),
            data_is_stale=True,
            stale_warning_surfaced=True,
        ),
        AgentObservation(
            case_id="operation-human-approval",
            answer="The request is pending human approval.",
            tool_calls=("request_pipeline_reprocessing",),
            operation_status="PENDING_APPROVAL",
        ),
    )


def test_complete_agent_evaluation_passes_and_states_method_limits() -> None:
    report = evaluate_agent_observations(DATASET, _passing_observations())

    assert report.evaluated_cases == report.passed_cases == 5
    assert all(result.passed for result in report.case_results)
    assert any(
        "not semantic groundedness" in limitation for limitation in report.method_limitations
    )


def test_unauthorized_citation_and_missing_stale_warning_fail_explicitly() -> None:
    observations = list(_passing_observations())
    observations[1] = AgentObservation(
        case_id="knowledge-restricted-refusal",
        answer="Leaked legal evidence.",
        tool_calls=("search_enterprise_knowledge",),
        citation_ids=("DOC-LEGAL#secret",),
        evidence_by_citation=(("DOC-LEGAL#secret", "restricted legal investigation"),),
        authorized_citation_ids=frozenset(),
    )
    observations[2] = AgentObservation(
        case_id="analytics-account-overdue",
        answer="A-1001 has an overdue amount of 2500.",
        tool_calls=("run_governed_sql",),
        data_is_stale=True,
        stale_warning_surfaced=False,
    )

    report = evaluate_agent_observations(DATASET, tuple(observations))
    failures = {result.case_id: result.failures for result in report.case_results}

    assert report.passed_cases == 3
    assert "unauthorized evidence or citation returned" in failures["knowledge-restricted-refusal"]
    assert "stale data was not disclosed" in failures["analytics-account-overdue"]


def test_missing_duplicate_or_unexpected_observations_are_rejected() -> None:
    observations = _passing_observations()
    with pytest.raises(ValueError, match="complete evaluation dataset"):
        evaluate_agent_observations(DATASET, observations[:-1])
    with pytest.raises(ValueError, match="unique case IDs"):
        evaluate_agent_observations(DATASET, observations + (observations[0],))
