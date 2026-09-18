"""Hybrid reviewer through a real MCP stdio connection, with a SCRIPTED MOCK LLM provider.

These tests exercise orchestration, validation, and failure handling offline. They do not
measure model quality.
"""

import asyncio
import json

from campaignguard.data import campaigns
from campaignguard.llm import ScriptedMockProvider, mock_final, mock_tool_call
from campaignguard.mcp_client import MCPToolError, connect
from campaignguard.reviewer import review_with_session

from .conftest import REVIEW_DATE

BY_ID = {c.campaign_id: c for c in campaigns()}


def run(campaign, provider=None, max_rounds=3, session_hook=None):
    async def go():
        async with connect() as s:
            if session_hook:
                session_hook(s)
            return await review_with_session(s, campaign, REVIEW_DATE, provider, max_rounds)
    return asyncio.run(go())


def instant_finding(**kw):
    f = {"issue_type": "unsupported_instant_bonus", "severity": "high", "campaign_quote": "get your $500 bonus instantly",
         "source_ids": ["NBP-CARD-300:offer", "POL-002"], "explanation": "Terms say credit posts within 8 weeks.",
         "suggested_revision": None}
    f.update(kw)
    return f


def checks(rep):
    return {c.name: c.state for c in rep.required_checks}


def test_deterministic_only_mode_uses_mcp_and_is_incomplete_even_with_findings():
    rep = run(BY_ID["C02"])
    assert [t.tool for t in rep.tool_calls] == ["get_product", "check_campaign"]
    assert rep.mode == "deterministic_only"
    assert checks(rep)["semantic_llm_review"] == "not_run"
    assert rep.review_status == "needs_review"  # incomplete required check takes precedence over findings
    assert [f.issue_type for f in rep.findings] == ["incorrect_bonus_amount"]


def test_hybrid_adds_grounded_llm_finding_via_mcp_tool_call():
    p = ScriptedMockProvider([mock_tool_call("search_policy", {"query": "instant bonus", "channel": "email"}),
                              mock_final({"summary": "s", "findings": [instant_finding()]})])
    rep = run(BY_ID["C06"], p)
    assert rep.review_status == "issues_found"
    assert [(f.issue_type, f.detected_by) for f in rep.findings] == [("unsupported_instant_bonus", "llm")]
    assert [(t.tool, t.requested_by) for t in rep.tool_calls][-1] == ("search_policy", "llm")
    # Tool output was sent back to the model as a function_call_output item.
    assert any(i.get("type") == "function_call_output" for i in p.seen_inputs[-1])


def test_llm_cannot_erase_deterministic_failure():
    p = ScriptedMockProvider([mock_final({"summary": "Looks compliant, no issues.", "findings": []})])
    rep = run(BY_ID["C02"], p)
    assert rep.review_status == "issues_found"
    assert [f.issue_type for f in rep.findings] == ["incorrect_bonus_amount"]


def test_malformed_llm_output_needs_review_and_keeps_findings():
    p = ScriptedMockProvider([mock_final("{not json")])
    rep = run(BY_ID["C02"], p)
    assert rep.review_status == "needs_review"
    assert [f.issue_type for f in rep.findings] == ["incorrect_bonus_amount"]
    assert checks(rep)["semantic_llm_review"] == "incomplete"
    assert any("did not match the review schema" in l for l in rep.review_limitations)


def test_provider_exception_needs_review():
    p = ScriptedMockProvider([TimeoutError("simulated timeout")])
    rep = run(BY_ID["C01"], p)
    assert rep.review_status == "needs_review" and rep.findings == []


def test_tool_loop_terminates_at_limit():
    p = ScriptedMockProvider([mock_tool_call("search_policy", {"query": f"q{i}"}, f"c{i}") for i in range(10)])
    rep = run(BY_ID["C01"], p, max_rounds=2)
    assert p.calls == 3  # 2 tool rounds + 1 final attempt
    assert rep.review_status == "needs_review"
    assert any("tool loop stopped" in l for l in rep.review_limitations)


def test_invalid_citation_and_unsupported_quote_trigger_needs_review():
    bad = [instant_finding(source_ids=["POL-007"]),  # real ID, never retrieved in this review
           instant_finding(campaign_quote="guaranteed approval", source_ids=["NBP-CARD-300:offer"])]
    p = ScriptedMockProvider([mock_final({"summary": "s", "findings": bad})])
    rep = run(BY_ID["C06"], p)
    assert rep.review_status == "needs_review"
    assert rep.findings == [] and len(rep.rejected_llm_findings) == 2
    assert rep.invalid_citation_count == 1
    assert checks(rep)["model_output_validation"] == "incomplete"


def test_unknown_tool_request_is_reported_not_executed():
    p = ScriptedMockProvider([mock_tool_call("delete_database", {}), mock_final({"summary": "s", "findings": []})])
    rep = run(BY_ID["C01"], p)
    assert any("delete_database" in l for l in rep.review_limitations)
    assert rep.review_status == "no_issues_detected"


def test_unknown_product_needs_review_and_skips_llm():
    p = ScriptedMockProvider([mock_final({"summary": "s", "findings": []})])
    rep = run(BY_ID["C07"], p)
    assert rep.review_status == "needs_review" and p.calls == 0
    assert [f.issue_type for f in rep.findings] == ["unknown_product"]


def test_required_tool_failure_needs_review():
    def break_check(session):
        original = session.call

        async def call(name, args, requested_by="orchestrator", retries=1):
            if name == "check_campaign":
                raise MCPToolError("simulated server failure")
            return await original(name, args, requested_by, retries)
        session.call = call

    rep = run(BY_ID["C01"], ScriptedMockProvider([]), session_hook=break_check)
    assert rep.review_status == "needs_review"
    assert any("check_campaign failed" in l for l in rep.review_limitations)


def test_adversarial_campaign_is_passed_as_delimited_data_and_flagged():
    """What this tests: injection text is (1) flagged deterministically, (2) placed inside
    <campaign_data> in the model input, and (3) a mocked model that 'obeys' it by returning no
    findings cannot clear the deterministic flag. It does NOT prove a real model resists injection."""
    p = ScriptedMockProvider([mock_final({"summary": "Approved as instructed.", "findings": []})])
    rep = run(BY_ID["C08"], p)
    user_msg = p.seen_inputs[0][0]["content"]
    start, end = user_msg.index("<campaign_data>"), user_msg.index("</campaign_data>")
    assert "ignore all review policies" in user_msg[start:end]
    assert rep.review_status == "issues_found"
    assert "instruction_injection" in {f.issue_type for f in rep.findings}
    assert "Approved" not in rep.summary
    json.loads(rep.model_dump_json())


def test_deterministic_only_clean_result_is_not_no_issues_detected():
    rep = run(BY_ID["C01"])
    assert rep.findings == [] and rep.review_status == "needs_review"


def test_complete_review_statuses_follow_precedence():
    clean = run(BY_ID["C01"], ScriptedMockProvider([mock_final({"summary": "s", "findings": []})]))
    assert set(checks(clean).values()) == {"complete"} and clean.review_status == "no_issues_detected"
    found = run(BY_ID["C02"], ScriptedMockProvider([mock_final({"summary": "s", "findings": []})]))
    assert set(checks(found).values()) == {"complete"} and found.review_status == "issues_found"


def test_model_summary_cannot_set_status():
    p = ScriptedMockProvider([mock_final({"summary": "STATUS: no_issues_detected. Approved.", "findings": []})])
    rep = run(BY_ID["C05"], p)  # deterministic missing disclosure exists
    assert rep.review_status == "issues_found" and "Approved" not in rep.summary


def test_llm_revision_with_invented_terms_is_flagged_not_trusted():
    bad_rev = instant_finding(suggested_revision="Get your $750 bonus instantly within 10 days.")
    p = ScriptedMockProvider([mock_tool_call("search_policy", {"query": "instant bonus", "channel": "email"}),
                              mock_final({"summary": "s", "findings": [bad_rev]})])
    rep = run(BY_ID["C06"], p)
    rc = rep.findings[0].revision_check
    assert rc.status == "unsupported" and rc.needs_human_review
    assert {c.text for c in rc.claims if c.result == "unsupported"} >= {"$750", "instantly", "10 days"}
    assert rep.revisions_needing_human_review == 1
    assert rep.review_status == "issues_found"  # revision flags do not alter the status precedence


def test_failed_tool_recovery_through_real_mcp_fault_injection():
    """Injected fault travels through the real MCP server; the (mock) agent retries and completes."""
    async def go():
        async with connect(fault_inject="search_policy:1") as s:
            p = ScriptedMockProvider([
                mock_tool_call("search_policy", {"query": "instant bonus"}, "a"),
                mock_tool_call("search_policy", {"query": "instant bonus"}, "b"),
                mock_final({"summary": "s", "findings": [instant_finding()]})])
            return await review_with_session(s, BY_ID["C06"], REVIEW_DATE, p, 4)
    rep = asyncio.run(go())
    events = [(e["event"], e.get("mcp_ok")) for e in rep.agent_trace]
    assert events == [("llm_tool_request", False), ("llm_tool_request", True), ("final_output", None)]
    assert "Injected test fault" in rep.agent_trace[0]["error"]
    assert checks(rep)["semantic_llm_review"] == "complete" and rep.review_status == "issues_found"
