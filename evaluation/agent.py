"""Rule-based agent, citation, and grounding evaluation without an external judge."""

from __future__ import annotations

from dataclasses import dataclass

from evaluation.dataset import EvaluationCase, EvaluationDataset


@dataclass(frozen=True)
class AgentObservation:
    case_id: str
    answer: str
    tool_calls: tuple[str, ...]
    citation_ids: tuple[str, ...] = ()
    evidence_by_citation: tuple[tuple[str, str], ...] = ()
    authorized_citation_ids: frozenset[str] = frozenset()
    asserted_facts: tuple[str, ...] = ()
    data_is_stale: bool = False
    stale_warning_surfaced: bool = False
    operation_status: str | None = None


@dataclass(frozen=True)
class AgentCaseResult:
    case_id: str
    passed: bool
    tool_routing_passed: bool
    citation_presence_passed: bool
    citation_existence_passed: bool
    authorization_passed: bool
    deterministic_fact_support_passed: bool
    freshness_disclosure_passed: bool
    operation_boundary_passed: bool
    failures: tuple[str, ...]


@dataclass(frozen=True)
class AgentEvaluationReport:
    dataset_id: str
    dataset_version: int
    evaluated_cases: int
    passed_cases: int
    case_results: tuple[AgentCaseResult, ...]
    method_limitations: tuple[str, ...]


def _normalized(value: str) -> str:
    return " ".join(value.casefold().split())


def _evaluate_case(case: EvaluationCase, observation: AgentObservation) -> AgentCaseResult:
    evidence = dict(observation.evidence_by_citation)
    tool_routing = observation.tool_calls == case.expected_tools
    citation_presence = set(case.expected_citation_ids).issubset(observation.citation_ids)
    citation_existence = all(citation in evidence for citation in observation.citation_ids)
    authorization = set(observation.citation_ids).issubset(observation.authorized_citation_ids)
    if case.expected_access == "REFUSE":
        authorization = authorization and not observation.citation_ids and not evidence

    answer = _normalized(observation.answer)
    cited_text = _normalized(" ".join(evidence.values()))
    fact_support = all(
        _normalized(fact) in answer and _normalized(fact) in cited_text
        for fact in observation.asserted_facts
    )
    freshness = not observation.data_is_stale or observation.stale_warning_surfaced
    expected_status = (case.expected_structured_result or {}).get("status")
    operation = expected_status is None or observation.operation_status == expected_status

    checks = {
        "tool routing mismatch": tool_routing,
        "required citation missing": citation_presence,
        "citation does not exist in returned evidence": citation_existence,
        "unauthorized evidence or citation returned": authorization,
        "asserted fact lacks deterministic cited-text support": fact_support,
        "stale data was not disclosed": freshness,
        "operation crossed the expected approval boundary": operation,
    }
    failures = tuple(message for message, passed in checks.items() if not passed)
    return AgentCaseResult(
        case_id=case.case_id,
        passed=not failures,
        tool_routing_passed=tool_routing,
        citation_presence_passed=citation_presence,
        citation_existence_passed=citation_existence,
        authorization_passed=authorization,
        deterministic_fact_support_passed=fact_support,
        freshness_disclosure_passed=freshness,
        operation_boundary_passed=operation,
        failures=failures,
    )


def evaluate_agent_observations(
    dataset: EvaluationDataset,
    observations: tuple[AgentObservation, ...],
) -> AgentEvaluationReport:
    """Evaluate the complete dataset with deterministic checks, not answer-quality scoring."""

    observed_ids = [observation.case_id for observation in observations]
    if len(observed_ids) != len(set(observed_ids)):
        raise ValueError("agent observations must have unique case IDs")
    by_case = {observation.case_id: observation for observation in observations}
    expected_ids = {case.case_id for case in dataset.cases}
    if set(by_case) != expected_ids:
        raise ValueError("agent observations must cover the complete evaluation dataset")
    results = tuple(_evaluate_case(case, by_case[case.case_id]) for case in dataset.cases)
    return AgentEvaluationReport(
        dataset_id=dataset.dataset_id,
        dataset_version=dataset.version,
        evaluated_cases=len(results),
        passed_cases=sum(result.passed for result in results),
        case_results=results,
        method_limitations=(
            "No hosted model or external judge was called.",
            "Fact support is deterministic phrase containment, not semantic groundedness.",
            "Passing results do not establish human answer quality or production security.",
        ),
    )
