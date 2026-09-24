"""Execute and persist retrieval evaluation against generated governed knowledge."""

from __future__ import annotations

import argparse
import json
import tempfile
from dataclasses import asdict, dataclass
from datetime import UTC, date, datetime
from pathlib import Path
from typing import Any

from data.generate_enterprise_batch import generate_batch
from enterprise_platform.chunking import chunk_document
from enterprise_platform.document_versions import resolve_document_versions
from enterprise_platform.documents import parse_and_validate_document
from enterprise_platform.embedding_lineage import embed_chunks
from enterprise_platform.embeddings import DeterministicLocalEmbeddingProvider
from enterprise_platform.governance import GovernedRetriever, PrincipalContext
from enterprise_platform.lexical_index import LocalLexicalIndex
from enterprise_platform.retrieval import LocalRetriever, RetrievalMode
from enterprise_platform.vector_store import LocalVectorIndex
from evaluation.dataset import EvaluationDataset, load_evaluation_dataset
from evaluation.retrieval import RankedResult, RetrievalComparison, compare_retrieval_modes

ROOT = Path(__file__).resolve().parents[1]
DATASET_PATH = ROOT / "evaluation" / "datasets" / "enterprise_agent_v1.json"


@dataclass(frozen=True)
class OfflineEvaluationReport:
    dataset_id: str
    dataset_version: int
    generated_from_scenario: str
    retrieval: RetrievalComparison
    restricted_refusal_passed: bool
    limitations: tuple[str, ...]


def _build_retriever(workdir: Path) -> GovernedRetriever:
    business_date = date(2026, 9, 18)
    batch = generate_batch(
        workdir / "input",
        business_date=business_date,
        batch_id="evaluation-001",
        seed=91,
        accounts=3,
        scenario="unauthorized-document",
    )
    normalized: list[dict[str, Any]] = []
    for line in (batch / "documents/documents.jsonl").read_text(encoding="utf-8").splitlines():
        document, violation = parse_and_validate_document(line)
        if violation is not None or document is None:
            raise RuntimeError("evaluation fixture violated its document contract")
        normalized.append(document)
    resolved = resolve_document_versions(normalized, as_of_date=business_date)
    chunks = [chunk for document in resolved.documents for chunk in chunk_document(document)]
    provider = DeterministicLocalEmbeddingProvider()
    embedded = embed_chunks(
        chunks,
        provider,
        embedded_at=datetime.combine(business_date, datetime.min.time(), UTC),
    )
    return GovernedRetriever(
        LocalRetriever(LocalVectorIndex(embedded), LocalLexicalIndex(embedded)),
        provider,
    )


def _positive_results(
    dataset: EvaluationDataset,
    retriever: GovernedRetriever,
    mode: RetrievalMode,
) -> tuple[RankedResult, ...]:
    results: list[RankedResult] = []
    for case in dataset.cases:
        if not case.relevant_chunk_ids:
            continue
        result = retriever.search(
            case.question,
            principal=PrincipalContext(case.user_id, frozenset(case.groups), case.region),
            mode=mode,
            account_id=case.account_id,
            k=3,
            candidate_k=20,
        )
        results.append(RankedResult(case.case_id, tuple(item.chunk_id for item in result.evidence)))
    return tuple(results)


def run_offline_evaluation(workdir: Path, output_path: Path) -> OfflineEvaluationReport:
    """Run every retrieval mode and mandatory restricted-access case locally."""

    dataset = load_evaluation_dataset(DATASET_PATH)
    retriever = _build_retriever(workdir)
    comparison = compare_retrieval_modes(
        dataset,
        {mode: _positive_results(dataset, retriever, mode) for mode in RetrievalMode},
        k=3,
    )
    restricted_case = next(
        case for case in dataset.cases if case.case_id == "knowledge-restricted-refusal"
    )
    refusal = retriever.search(
        restricted_case.question,
        principal=PrincipalContext(
            restricted_case.user_id,
            frozenset(restricted_case.groups),
            restricted_case.region,
        ),
        account_id=restricted_case.account_id,
    )
    report = OfflineEvaluationReport(
        dataset_id=dataset.dataset_id,
        dataset_version=dataset.version,
        generated_from_scenario="unauthorized-document",
        retrieval=comparison,
        restricted_refusal_passed=not refusal.evidence,
        limitations=(
            "Local deterministic token-hash embeddings are not a semantic production model.",
            "Metrics are offline contract evidence, not production latency or ANN recall.",
            "Agent behavior is covered separately by deterministic ADK and policy tests.",
        ),
    )
    output_path.parent.mkdir(parents=True, exist_ok=True)
    payload = json.dumps(asdict(report), sort_keys=True, indent=2, allow_nan=False) + "\n"
    output_path.write_text(payload, encoding="utf-8", newline="\n")
    return report


def main() -> None:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument(
        "--output",
        type=Path,
        default=ROOT / "evaluation" / "reports" / "baseline_v1.json",
    )
    args = parser.parse_args()
    with tempfile.TemporaryDirectory(prefix="enterprise-platform-evaluation-") as directory:
        report = run_offline_evaluation(Path(directory), args.output)
    print(json.dumps(asdict(report), sort_keys=True, indent=2))


if __name__ == "__main__":
    main()
