"""Fail-closed policy and bounded execution for local analytical SQL."""

from __future__ import annotations

import hashlib
import re
from collections.abc import Iterable
from dataclasses import dataclass
from typing import Any

import duckdb

_COMMENT = re.compile(r"--|/\*|\*/")
_QUALIFIED_RELATION = re.compile(r"\b(?:from|join)\s+[\w\"`]+\s*\.", re.IGNORECASE)
_STRING_RELATION = re.compile(r"\b(?:from|join)\s*['\"]", re.IGNORECASE)
_TABLE_FUNCTION = re.compile(r"\b(?:from|join)\s+[a-z_]\w*\s*\(", re.IGNORECASE)
_UNSAFE_FUNCTION = re.compile(
    r"\b(?:read_\w+|glob|sniff_csv|query|query_table|write_blob|"
    r"getenv|current_setting|duckdb_\w+)\s*\(",
    re.IGNORECASE,
)


def _replace_positional_parameters(sql: str) -> str:
    """Replace unquoted DB-API placeholders with NULL for relation analysis only."""

    output: list[str] = []
    quote: str | None = None
    index = 0
    while index < len(sql):
        character = sql[index]
        if quote:
            output.append(character)
            if character == quote:
                if index + 1 < len(sql) and sql[index + 1] == quote:
                    output.append(sql[index + 1])
                    index += 1
                else:
                    quote = None
        elif character in {"'", '"'}:
            quote = character
            output.append(character)
        elif character == "?":
            output.append("NULL")
        else:
            output.append(character)
        index += 1
    return "".join(output)


class SQLPolicyViolation(ValueError):
    """Raised before execution when a query is outside the analytical policy."""


@dataclass(frozen=True)
class ValidatedSQL:
    sql: str
    referenced_relations: frozenset[str]


@dataclass(frozen=True)
class QueryResult:
    query_id: str
    columns: tuple[str, ...]
    rows: tuple[tuple[Any, ...], ...]
    truncated: bool


class ReadOnlySQLPolicy:
    """Permit one bounded SELECT over an explicit set of unqualified local views."""

    def __init__(
        self,
        allowed_relations: Iterable[str],
        *,
        max_query_characters: int = 20_000,
    ) -> None:
        relations = frozenset(relation.casefold() for relation in allowed_relations)
        if not relations or any(not relation.isidentifier() for relation in relations):
            raise ValueError("allowed relations must be non-empty, unqualified identifiers")
        if max_query_characters < 1:
            raise ValueError("max_query_characters must be positive")
        self.allowed_relations = relations
        self.max_query_characters = max_query_characters

    def validate(self, sql: str) -> ValidatedSQL:
        candidate = sql.strip()
        if not candidate:
            raise SQLPolicyViolation("query must not be empty")
        if len(candidate) > self.max_query_characters:
            raise SQLPolicyViolation("query exceeds the configured character limit")
        if _COMMENT.search(candidate):
            raise SQLPolicyViolation("SQL comments are not permitted")
        if not re.match(r"^(?:select|with)\b", candidate, re.IGNORECASE):
            raise SQLPolicyViolation("only SELECT statements are permitted")
        if _QUALIFIED_RELATION.search(candidate):
            raise SQLPolicyViolation("qualified relations are not permitted")
        if _STRING_RELATION.search(candidate):
            raise SQLPolicyViolation("file-backed relations are not permitted")
        if _TABLE_FUNCTION.search(candidate) or _UNSAFE_FUNCTION.search(candidate):
            raise SQLPolicyViolation(
                "table, file, environment, and dynamic SQL functions are denied"
            )

        parser = duckdb.connect(":memory:")
        try:
            statements = parser.extract_statements(candidate)
            if len(statements) != 1:
                raise SQLPolicyViolation("exactly one SQL statement is required")
            if statements[0].type != duckdb.StatementType.SELECT:
                raise SQLPolicyViolation("only SELECT statements are permitted")
            try:
                referenced = frozenset(
                    name.casefold()
                    for name in parser.get_table_names(_replace_positional_parameters(candidate))
                )
            except duckdb.Error as error:
                raise SQLPolicyViolation("query could not be safely analyzed") from error
        except duckdb.ParserException as error:
            raise SQLPolicyViolation("query is not valid SQL") from error
        finally:
            parser.close()

        denied = referenced - self.allowed_relations
        if denied:
            raise SQLPolicyViolation(
                "query references relations outside the allowlist: " + ", ".join(sorted(denied))
            )
        return ValidatedSQL(candidate, referenced)


def execute_governed_query(
    connection: duckdb.DuckDBPyConnection,
    policy: ReadOnlySQLPolicy,
    sql: str,
    parameters: list[Any] | tuple[Any, ...] | None = None,
    *,
    max_rows: int = 100,
) -> QueryResult:
    """Validate a SELECT, execute it with parameters, and enforce a hard output cap."""

    if max_rows < 1:
        raise ValueError("max_rows must be positive")
    validated = policy.validate(sql)
    inner_sql = validated.sql.rstrip().removesuffix(";").rstrip()
    bounded_sql = f"SELECT * FROM ({inner_sql}) AS governed_query LIMIT {max_rows + 1}"
    cursor = connection.execute(bounded_sql, parameters or [])
    rows = cursor.fetchall()
    columns = tuple(description[0] for description in cursor.description)
    normalized_parameters = repr(tuple(parameters or ()))
    query_id = hashlib.sha256(
        f"{validated.sql}\x1f{normalized_parameters}\x1f{max_rows}".encode()
    ).hexdigest()
    return QueryResult(
        query_id=query_id,
        columns=columns,
        rows=tuple(rows[:max_rows]),
        truncated=len(rows) > max_rows,
    )
