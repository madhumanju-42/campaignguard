"""OpenAI adapter against a MOCKED HTTP transport (no network, no key).

Verifies request shape (tools translated from MCP, JSON-schema output format, tool results
sent back as function_call_output) and response parsing. It does NOT verify that the live
API accepts the request; see tests/test_live_smoke.py for that.
"""

import asyncio
import json

import httpx2 as httpx  # openai 3.x uses the httpx2 package

from campaignguard.data import campaigns
from campaignguard.llm import OpenAIProvider
from campaignguard.mcp_client import connect
from campaignguard.reviewer import review_with_session

from .conftest import REVIEW_DATE


def _resp(output, rid):
    return {"id": rid, "object": "response", "created_at": 0, "status": "completed", "model": "mock-model",
            "output": output, "parallel_tool_calls": True, "tool_choice": "auto", "tools": [],
            "error": None, "incomplete_details": None, "instructions": None, "metadata": {},
            "temperature": 1.0, "top_p": 1.0}


def test_responses_adapter_round_trip_with_mock_transport():
    bodies = []
    final = {"summary": "s", "findings": [{
        "issue_type": "unsupported_instant_bonus", "severity": "high", "campaign_quote": "get your $500 bonus instantly",
        "source_ids": ["NBP-CARD-300:offer", "POL-002"], "explanation": "timing unsupported", "suggested_revision": None}]}

    def handler(request: httpx.Request) -> httpx.Response:
        body = json.loads(request.content)
        bodies.append(body)
        if len(bodies) == 1:
            out = [{"type": "function_call", "id": "fc_1", "call_id": "call_1", "name": "search_policy",
                    "arguments": json.dumps({"query": "instant bonus timing", "channel": "email"}), "status": "completed"}]
        else:
            out = [{"type": "message", "id": "msg_1", "role": "assistant", "status": "completed",
                    "content": [{"type": "output_text", "text": json.dumps(final), "annotations": []}]}]
        return httpx.Response(200, json=_resp(out, f"resp_{len(bodies)}"))

    provider = OpenAIProvider("sk-test-mock", "mock-model", 10, 0,
                              http_client=httpx.AsyncClient(transport=httpx.MockTransport(handler)))
    c06 = next(c for c in campaigns() if c.campaign_id == "C06")

    async def go():
        async with connect() as s:
            return await review_with_session(s, c06, REVIEW_DATE, provider, 3)
    rep = asyncio.run(go())

    first, second = bodies
    assert {t["name"] for t in first["tools"]} == {"get_product", "search_policy", "check_campaign"}
    assert first["text"]["format"]["type"] == "json_schema"
    assert "<campaign_data>" in first["input"][0]["content"]
    outputs = [i for i in second["input"] if i.get("type") == "function_call_output"]
    assert outputs and outputs[0]["call_id"] == "call_1" and "POL-002" in outputs[0]["output"]
    assert "async_" not in json.dumps(second["input"])
    assert rep.mode == "live_llm" and rep.review_status == "issues_found"
    assert [(f.issue_type, f.detected_by) for f in rep.findings] == [("unsupported_instant_bonus", "llm")]
