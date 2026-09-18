"""Real MCP client <-> server over local stdio (spawns the server subprocess)."""

import asyncio

from campaignguard.data import campaigns
from campaignguard.mcp_client import MCPToolError, connect


def test_discovery_and_invocation_over_stdio():
    async def go():
        async with connect() as s:
            assert s.tool_names == {"get_product", "search_policy", "check_campaign"}
            schema = next(t for t in s.tools if t.name == "check_campaign").input_schema
            assert "campaign" in schema["properties"]

            prod = await s.call("get_product", {"product_id": "NBP-CHK-100"})
            assert prod["found"] and prod["product"]["offer"]["bonus_amount"] == 300
            assert any(x["source_id"] == "NBP-CHK-100:offer" for x in prod["sources"])

            pol = await s.call("search_policy", {"query": "instant bonus timing", "channel": "sms", "top_k": 2})
            assert pol["results"][0]["policy_id"] == "POL-002" and pol["results"][0]["score"] > 0

            c02 = next(c for c in campaigns() if c.campaign_id == "C02")
            chk = await s.call("check_campaign", {"campaign": c02.model_dump(), "review_date": "2026-09-15"})
            assert [f["issue_type"] for f in chk["findings"]] == ["incorrect_bonus_amount"]

            unknown = await s.call("get_product", {"product_id": "NOPE"})
            assert unknown["found"] is False

            try:
                await s.call("search_policy", {"query": "fees", "top_k": 99})
                raise AssertionError("expected tool error")
            except MCPToolError:
                pass
            try:
                await s.call("check_campaign", {"campaign": {"campaign_id": "x"}})  # schema violation
                raise AssertionError("expected validation error")
            except MCPToolError:
                pass
            assert [(l.tool, l.ok) for l in s.log][-2:] == [("search_policy", False), ("check_campaign", False)]
            assert s.log[0].source_ids and s.log[0].duration_ms >= 0
    asyncio.run(go())


def test_connect_timeout_is_separate_from_tool_timeout():
    """Server startup (imports) gets a longer budget than an individual tool call."""
    from campaignguard.config import MCP_CONNECT_TIMEOUT_S, MCP_TOOL_TIMEOUT_S

    assert MCP_CONNECT_TIMEOUT_S >= 60 and MCP_CONNECT_TIMEOUT_S > MCP_TOOL_TIMEOUT_S
