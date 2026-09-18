"""LLM provider adapter (OpenAI Responses API) plus MCP -> provider tool-schema translation.

The reviewer depends only on the small `LLMProvider` interface, so offline tests can use a
scripted mock provider. Mock responses are never presented as live results.
"""

from __future__ import annotations

import json
from dataclasses import dataclass, field
from typing import Any, Protocol

from .schemas import LLM_ISSUE_TYPES


@dataclass
class ToolRequest:
    call_id: str
    name: str
    arguments: str  # raw JSON string produced by the model


@dataclass
class LLMTurn:
    tool_requests: list[ToolRequest] = field(default_factory=list)
    text: str | None = None
    raw_output: list[dict] = field(default_factory=list)  # items to append to the conversation


class LLMProvider(Protocol):
    model: str
    is_mock: bool

    async def respond(self, instructions: str, input_items: list[dict], tools: list[dict]) -> LLMTurn: ...


def mcp_tools_to_openai(tools: list) -> list[dict]:
    """Translate discovered MCP tool definitions into Responses API function tools.

    strict=False because MCP-generated JSON Schemas use optional fields and $defs that
    strict mode does not accept; arguments are validated by the MCP server instead.
    """
    return [
        {
            "type": "function",
            "name": t.name,
            "description": (t.description or "")[:1000],
            "parameters": t.input_schema,
            "strict": False,
        }
        for t in tools
    ]


REVIEW_OUTPUT_SCHEMA: dict[str, Any] = {
    "type": "object",
    "additionalProperties": False,
    "required": ["findings", "summary"],
    "properties": {
        "summary": {"type": "string"},
        "findings": {
            "type": "array",
            "items": {
                "type": "object",
                "additionalProperties": False,
                "required": ["issue_type", "severity", "campaign_quote", "source_ids", "explanation", "suggested_revision"],
                "properties": {
                    "issue_type": {"type": "string", "enum": list(LLM_ISSUE_TYPES)},
                    "severity": {"type": "string", "enum": ["low", "medium", "high"]},
                    "campaign_quote": {"type": ["string", "null"]},
                    "source_ids": {"type": "array", "items": {"type": "string"}},
                    "explanation": {"type": "string"},
                    "suggested_revision": {"type": ["string", "null"]},
                },
            },
        },
    },
}


class OpenAIProvider:
    is_mock = False

    def __init__(self, api_key: str, model: str, timeout_s: float, max_retries: int, http_client=None):
        from openai import AsyncOpenAI

        # The SDK retries connection errors, 408/409/429 and 5xx with backoff.
        kwargs = {"http_client": http_client} if http_client is not None else {}
        self._client = AsyncOpenAI(api_key=api_key, timeout=timeout_s, max_retries=max_retries, **kwargs)
        self.model = model

    async def respond(self, instructions: str, input_items: list[dict], tools: list[dict]) -> LLMTurn:
        response = await self._client.responses.create(
            model=self.model,
            instructions=instructions,
            input=input_items,
            tools=tools,
            text={"format": {"type": "json_schema", "name": "campaign_review",
                             "schema": REVIEW_OUTPUT_SCHEMA, "strict": True}},
        )
        turn = LLMTurn()
        for item in response.output:
            turn.raw_output.append(item.model_dump(by_alias=True, exclude_none=True))
            if item.type == "function_call":
                turn.tool_requests.append(ToolRequest(call_id=item.call_id, name=item.name, arguments=item.arguments))
        if not turn.tool_requests:
            turn.text = response.output_text
        return turn


class ScriptedMockProvider:
    """OFFLINE TEST MOCK. Replays pre-written turns; not a model."""

    is_mock = True

    def __init__(self, turns: list[LLMTurn | Exception], model: str = "mock-scripted"):
        self._turns = list(turns)
        self.model = model
        self.calls = 0
        self.seen_inputs: list[list[dict]] = []

    async def respond(self, instructions: str, input_items: list[dict], tools: list[dict]) -> LLMTurn:
        self.calls += 1
        self.seen_inputs.append(json.loads(json.dumps(input_items)))
        if not self._turns:
            raise RuntimeError("mock script exhausted")
        nxt = self._turns.pop(0)
        if isinstance(nxt, Exception):
            raise nxt
        return nxt


def mock_tool_call(name: str, args: dict, call_id: str = "call_1") -> LLMTurn:
    return LLMTurn(
        tool_requests=[ToolRequest(call_id=call_id, name=name, arguments=json.dumps(args))],
        raw_output=[{"type": "function_call", "call_id": call_id, "name": name, "arguments": json.dumps(args)}],
    )


def mock_final(payload: dict | str) -> LLMTurn:
    text = payload if isinstance(payload, str) else json.dumps(payload)
    return LLMTurn(text=text, raw_output=[{"type": "message", "role": "assistant", "content": [{"type": "output_text", "text": text}]}])
