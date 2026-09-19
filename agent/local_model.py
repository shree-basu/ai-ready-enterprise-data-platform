"""Deterministic ADK model used only for local tool-routing tests and demos."""

from __future__ import annotations

import hashlib
import json
import re
from collections.abc import AsyncGenerator
from typing import Any

from google.adk.models import BaseLlm, LlmRequest, LlmResponse
from google.adk.models._capabilities import LlmCapabilities
from google.genai import types


class DeterministicLocalAdkModel(BaseLlm):
    """Route predefined intents without inference, network access, or credentials."""

    model: str = "deterministic-local-adk-v1"

    @property
    def capabilities(self) -> LlmCapabilities:
        return LlmCapabilities(output_schema_and_tools=True)

    @staticmethod
    def _user_text(request: LlmRequest) -> str:
        for content in reversed(request.contents):
            if content.role != "user" or not content.parts:
                continue
            text = " ".join(part.text or "" for part in content.parts).strip()
            if text:
                return text
        return ""

    @staticmethod
    def _function_response(request: LlmRequest) -> types.FunctionResponse | None:
        for content in reversed(request.contents):
            for part in reversed(content.parts or []):
                if part.function_response is not None:
                    return part.function_response
        return None

    @staticmethod
    def _account_id(text: str) -> str:
        match = re.search(r"\bA-\d{4,}\b", text, flags=re.IGNORECASE)
        return "" if match is None else match.group(0).upper()

    @staticmethod
    def _tool_call(prompt: str) -> tuple[str, dict[str, Any]]:
        lowered = prompt.casefold()
        account_id = DeterministicLocalAdkModel._account_id(prompt)
        if "fresh" in lowered or "stale" in lowered:
            return "get_data_freshness", {}
        if "reprocess" in lowered or "rerun" in lowered:
            target = re.search(r"\brun-[a-z0-9-]+\b", prompt, flags=re.IGNORECASE)
            return (
                "request_pipeline_reprocessing",
                {
                    "target_run_id": target.group(0) if target else "run-unspecified",
                    "reason": "User requested governed local reprocessing review",
                },
            )
        if any(term in lowered for term in ("overdue", "invoice balance", "account health")):
            where = f" WHERE account_id = '{account_id}'" if account_id else ""
            return (
                "run_governed_sql",
                {
                    "query": (
                        "SELECT account_id, overdue_invoices, overdue_amount "
                        f"FROM serving_account_health{where} ORDER BY account_id"
                    )
                },
            )
        return (
            "search_enterprise_knowledge",
            {"query": prompt, "account_id": account_id, "top_k": 5},
        )

    @staticmethod
    def _final_text(response: types.FunctionResponse) -> str:
        payload: Any = response.response or {}
        if isinstance(payload, dict) and set(payload) == {"result"}:
            payload = payload["result"]
        if not isinstance(payload, dict):
            return "The governed tool returned an invalid local response."

        name = response.name or ""
        if name == "search_enterprise_knowledge":
            evidence = payload.get("evidence") or []
            if not evidence:
                return (
                    f"No authorized knowledge evidence was found ({payload.get('empty_reason')})."
                )
            citations = ", ".join(str(item["citation_id"]) for item in evidence)
            titles = ", ".join(str(item["title"]) for item in evidence)
            return (
                f"Retrieved governed document data from {titles}. Citations: {citations}. "
                "Retrieved document text is untrusted data, not agent instructions."
            )
        if name == "run_governed_sql":
            if payload.get("status") != "OK":
                return f"Governed SQL was {payload.get('status')}: {payload.get('error_code')}."
            return (
                f"Governed analytics query {payload.get('query_id')} returned "
                f"{json.dumps(payload.get('rows'), sort_keys=True)}. "
                f"Data freshness: {payload.get('data_freshness_timestamp')}."
            )
        if name == "get_data_freshness":
            discrepancies = payload.get("discrepancies") or []
            prefix = "WARNING: " if not payload.get("safe_for_answer") else ""
            detail = "; ".join(str(item) for item in discrepancies) or "all sources are fresh"
            return f"{prefix}Freshness status {payload.get('status')}: {detail}."
        if name == "request_pipeline_reprocessing":
            return (
                f"Created operation request {payload.get('request_id')} with status "
                f"{payload.get('status')}; a separate human approver is required."
            )
        return "The deterministic local model received an unsupported tool response."

    async def generate_content_async(
        self, llm_request: LlmRequest, stream: bool = False
    ) -> AsyncGenerator[LlmResponse, None]:
        del stream
        function_response = self._function_response(llm_request)
        if function_response is not None:
            part = types.Part.from_text(text=self._final_text(function_response))
        else:
            prompt = self._user_text(llm_request)
            tool_name, arguments = self._tool_call(prompt)
            call_id = hashlib.sha256(
                json.dumps(
                    {"tool": tool_name, "arguments": arguments},
                    sort_keys=True,
                    separators=(",", ":"),
                ).encode()
            ).hexdigest()[:24]
            part = types.Part(
                function_call=types.FunctionCall(
                    id=call_id,
                    name=tool_name,
                    args=arguments,
                )
            )
        yield LlmResponse(
            model_version=self.model,
            content=types.Content(role="model", parts=[part]),
            partial=False,
        )
