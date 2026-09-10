"""Apache Beam structured ingestion graph, executed locally with DirectRunner only."""

from __future__ import annotations

from collections.abc import Iterable
from dataclasses import dataclass
from datetime import date, datetime
from decimal import Decimal
from typing import Any

import apache_beam as beam
from apache_beam import pvalue

from enterprise_platform.dedupe import canonical_record_hash
from enterprise_platform.quality import ContractViolation, natural_key, validate_structured_row
from enterprise_platform.structured import (
    STRUCTURED_ENTITIES,
    attach_lineage,
    quarantine_record,
)


@dataclass(frozen=True)
class BeamStructuredOutputs:
    accepted: dict[str, beam.PCollection]
    quarantined: beam.PCollection
    reconciliation: beam.PCollection


class _ValidateRow(beam.DoFn):
    def __init__(self, entity: str, business_date: date, batch_id: str) -> None:
        self.entity = entity
        self.business_date = business_date
        self.batch_id = batch_id

    def process(self, raw_record: dict[str, Any]):
        accepted, violation = validate_structured_row(
            self.entity, raw_record, business_date=self.business_date
        )
        if accepted is not None:
            yield accepted
        else:
            assert violation is not None
            yield pvalue.TaggedOutput(
                "quarantine", quarantine_record(violation, batch_id=self.batch_id)
            )


class _ResolveDuplicates(beam.DoFn):
    def __init__(self, entity: str, batch_id: str) -> None:
        self.entity = entity
        self.batch_id = batch_id

    def process(self, item: tuple[tuple[str, ...], Iterable[dict[str, Any]]]):
        key, grouped_rows = item
        rows = sorted(
            grouped_rows,
            key=lambda row: (row["updated_at"], canonical_record_hash(row)),
            reverse=True,
        )
        yield rows[0]
        for duplicate in rows[1:]:
            violation = ContractViolation(
                entity=self.entity,
                code="DUPLICATE_NATURAL_KEY",
                message=f"superseded deterministic duplicate for natural key {key!r}",
                raw_record=duplicate,
            )
            yield pvalue.TaggedOutput(
                "quarantine", quarantine_record(violation, batch_id=self.batch_id)
            )


class _ResolveAccountReference(beam.DoFn):
    def __init__(self, entity: str, batch_id: str) -> None:
        self.entity = entity
        self.batch_id = batch_id

    def process(self, item: tuple[str, dict[str, list[Any]]]):
        account_id, grouped = item
        for row in grouped["rows"]:
            if grouped["accounts"]:
                yield row
            else:
                violation = ContractViolation(
                    entity=self.entity,
                    code="ORPHAN_ACCOUNT_REFERENCE",
                    message=f"account_id {account_id!r} is not accepted for this batch",
                    raw_record=row,
                )
                yield pvalue.TaggedOutput(
                    "quarantine", quarantine_record(violation, batch_id=self.batch_id)
                )


def _reconcile(item: tuple[str, dict[str, list[int]]]) -> dict[str, Any]:
    entity, grouped = item
    source = sum(grouped["source"])
    accepted = sum(grouped["accepted"])
    quarantined = sum(grouped["quarantine"])
    if source != accepted + quarantined:
        raise RuntimeError(f"Beam reconciliation failed for {entity}")
    return {
        "entity": entity,
        "source_rows": source,
        "accepted_rows": accepted,
        "quarantined_rows": quarantined,
        "balanced": True,
    }


def _key_part(value: Any) -> str:
    if isinstance(value, date | datetime):
        return value.isoformat()
    if isinstance(value, Decimal):
        return format(value, "f")
    return str(value)


def _beam_natural_key(entity: str, row: dict[str, Any]) -> tuple[str, ...]:
    return tuple(_key_part(value) for value in natural_key(entity, row))


def _attach_lineage(
    row: dict[str, Any], entity: str, business_date: date, batch_id: str
) -> dict[str, Any]:
    return attach_lineage(entity, row, business_date=business_date, batch_id=batch_id)


def build_structured_graph(
    pipeline: beam.Pipeline,
    raw_by_entity: dict[str, list[dict[str, Any]]],
    *,
    business_date: date,
    batch_id: str,
) -> BeamStructuredOutputs:
    """Build a bounded, cloud-free graph from explicitly supplied source rows."""

    if set(raw_by_entity) != set(STRUCTURED_ENTITIES):
        raise ValueError("raw input must contain exactly the structured entities")

    deduplicated: dict[str, beam.PCollection] = {}
    quarantine_collections: list[beam.PCollection] = []
    source_counts: list[beam.PCollection] = []
    for entity in STRUCTURED_ENTITIES:
        raw = pipeline | f"Create {entity}" >> beam.Create(raw_by_entity[entity])
        source_counts.append(
            raw
            | f"Source entity {entity}" >> beam.Map(lambda _, name=entity: name)
            | f"Count source {entity}" >> beam.combiners.Count.PerElement()
        )
        validated = raw | f"Validate {entity}" >> beam.ParDo(
            _ValidateRow(entity, business_date, batch_id)
        ).with_outputs("quarantine", main="accepted")
        quarantine_collections.append(validated.quarantine)
        resolved = (
            validated.accepted
            | f"Key {entity}"
            >> beam.Map(lambda row, name=entity: (_beam_natural_key(name, row), row))
            | f"Group {entity}" >> beam.GroupByKey()
            | f"Resolve duplicates {entity}"
            >> beam.ParDo(_ResolveDuplicates(entity, batch_id)).with_outputs(
                "quarantine", main="accepted"
            )
        )
        deduplicated[entity] = resolved.accepted
        quarantine_collections.append(resolved.quarantine)

    accounts_by_id = deduplicated["accounts"] | "Accepted account IDs" >> beam.Map(
        lambda row: (row["account_id"], True)
    )
    accepted_without_lineage: dict[str, beam.PCollection] = {"accounts": deduplicated["accounts"]}
    for entity in STRUCTURED_ENTITIES[1:]:
        joined = {
            "accounts": accounts_by_id,
            "rows": deduplicated[entity]
            | f"Key {entity} by account" >> beam.Map(lambda row: (row["account_id"], row)),
        } | f"Join {entity} to accounts" >> beam.CoGroupByKey()
        resolved = joined | f"Resolve {entity} account references" >> beam.ParDo(
            _ResolveAccountReference(entity, batch_id)
        ).with_outputs("quarantine", main="accepted")
        accepted_without_lineage[entity] = resolved.accepted
        quarantine_collections.append(resolved.quarantine)

    accepted = {
        entity: rows
        | f"Attach {entity} lineage"
        >> beam.Map(
            _attach_lineage,
            entity,
            business_date,
            batch_id,
        )
        for entity, rows in accepted_without_lineage.items()
    }
    quarantined = quarantine_collections | "Flatten quarantines" >> beam.Flatten()
    accepted_counts = [
        rows
        | f"Accepted entity {entity}" >> beam.Map(lambda _, name=entity: name)
        | f"Count accepted {entity}" >> beam.combiners.Count.PerElement()
        for entity, rows in accepted.items()
    ]
    quarantine_counts = (
        quarantined
        | "Quarantine entities" >> beam.Map(lambda row: row["entity"])
        | "Count quarantines" >> beam.combiners.Count.PerElement()
    )
    reconciliation = (
        {
            "source": source_counts | "Flatten source counts" >> beam.Flatten(),
            "accepted": accepted_counts | "Flatten accepted counts" >> beam.Flatten(),
            "quarantine": quarantine_counts,
        }
        | "Join reconciliation counts" >> beam.CoGroupByKey()
        | "Assert reconciliation" >> beam.Map(_reconcile)
    )
    return BeamStructuredOutputs(
        accepted=accepted,
        quarantined=quarantined,
        reconciliation=reconciliation,
    )
