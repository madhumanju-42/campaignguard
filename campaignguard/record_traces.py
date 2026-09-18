"""Record sanitized agent traces proving (or not) the live LLM -> MCP integration.

    python -m campaignguard.record_traces          # LIVE: requires OPENAI_API_KEY
    python -m campaignguard.record_traces --mock   # scripted mock; does NOT prove live integration

Scenarios
  1. success:   campaign C06; the model requests an MCP tool and gets a successful result.
  2. recovery:  campaign C18 with TEST fault injection (first search_policy call fails through
                the real MCP protocol); the model must continue after the error and finish with
                schema-valid output.

Writes results/traces/<live|mock>_<scenario>_trace.json and LIVE_VERIFICATION_STATUS.json.
Traces contain tool names, arguments, results metadata, and errors. They never contain API keys,
request headers, or model reasoning items.
"""

from __future__ import annotations

import argparse
import asyncio
import json
import sys
from datetime import datetime, timezone

from . import config
from .data import campaigns
from .llm import ScriptedMockProvider, mock_final, mock_tool_call
from .reviewer import default_provider, review_campaigns
from .schemas import ReviewReport
from .trace import sanitize

TRACE_DIR = config.RESULTS_DIR / "traces"
STATUS_PATH = TRACE_DIR / "LIVE_VERIFICATION_STATUS.json"
SCENARIOS = {
    "success": {"campaign_id": "C06", "fault_inject": None},
    "failed_tool_recovery": {"campaign_id": "C18", "fault_inject": "search_policy:1"},
}


def _now() -> str:
    return datetime.now(timezone.utc).isoformat(timespec="seconds")


def evaluate_criteria(scenario: str, rep: ReviewReport) -> dict[str, bool]:
    tr = rep.agent_trace
    llm_calls = [e for e in tr if e.get("event") == "llm_tool_request"]
    semantic_complete = any(c.name == "semantic_llm_review" and c.state == "complete" for c in rep.required_checks)
    llm_mcp_log = [t for t in rep.tool_calls if t.requested_by == "llm"]
    if scenario == "success":
        return {
            "model_requested_at_least_one_tool": bool(llm_calls),
            "mcp_call_from_model_succeeded": any(e.get("mcp_ok") for e in llm_calls),
            "call_went_through_mcp_client_log": any(t.ok for t in llm_mcp_log),
            "semantic_llm_review_complete": semantic_complete,
        }
    fail_idx = next((i for i, e in enumerate(tr) if e.get("event") == "llm_tool_request" and e.get("mcp_ok") is False
                     and "Injected test fault" in str(e.get("error", ""))), None)
    after = tr[fail_idx + 1:] if fail_idx is not None else []
    return {
        "injected_fault_reached_model": fail_idx is not None,
        "agent_continued_after_failure": any(e.get("event") in ("llm_tool_request", "final_output") for e in after),
        "final_output_schema_valid": any(e.get("event") == "final_output" and e.get("schema_valid") for e in after),
        "semantic_llm_review_complete": semantic_complete,
    }


def mock_provider(scenario: str) -> ScriptedMockProvider:
    finding = {"issue_type": "unsupported_instant_bonus", "severity": "high", "source_ids": ["POL-002"],
               "explanation": "Product terms do not support immediate posting.", "suggested_revision": None}
    if scenario == "success":
        return ScriptedMockProvider([
            mock_tool_call("search_policy", {"query": "instant bonus timing", "channel": "email", "top_k": 2}),
            mock_final({"summary": "mock", "findings": [{**finding, "campaign_quote": "get your $500 bonus instantly"}]}),
        ])
    return ScriptedMockProvider([
        mock_tool_call("search_policy", {"query": "bonus timing no waiting", "channel": "email", "top_k": 2}, "call_a"),
        mock_tool_call("search_policy", {"query": "bonus timing no waiting", "channel": "email", "top_k": 2}, "call_b"),
        mock_final({"summary": "mock", "findings": [{**finding, "campaign_quote": "posts the moment you hit the goal, no waiting"}]}),
    ])


async def record(scenario: str, use_mock: bool) -> dict:
    spec = SCENARIOS[scenario]
    campaign = next(c for c in campaigns() if c.campaign_id == spec["campaign_id"])
    provider = mock_provider(scenario) if use_mock else default_provider()
    rep = (await review_campaigns([campaign], config.DEFAULT_REVIEW_DATE, provider, fault_inject=spec["fault_inject"]))[0]
    criteria = evaluate_criteria(scenario, rep)
    return sanitize({
        "trace_type": "mock" if use_mock else "live",
        "llm_provider": "SCRIPTED MOCK - not a live model" if use_mock else "openai (Responses API)",
        "model": rep.model,
        "proves_live_integration": (not use_mock) and all(criteria.values()),
        "recorded_at_utc": _now(),
        "scenario": scenario,
        "campaign_id": campaign.campaign_id,
        "fault_injection": spec["fault_inject"],
        "sanitization": "API keys/headers never recorded; strings truncated to 300 chars; reasoning items excluded.",
        "criteria": criteria,
        "criteria_met": all(criteria.values()),
        "mcp_tool_calls": [t.model_dump() for t in rep.tool_calls],
        "agent_trace": rep.agent_trace,
        "review_status": rep.review_status,
        "required_checks": [c.model_dump() for c in rep.required_checks],
        "findings": [{"issue_type": f.issue_type, "detected_by": f.detected_by,
                      "revision_check": f.revision_check.status if f.revision_check else None} for f in rep.findings],
    }, max_str=400)


def _write_status(status: str, reason: str, files: list[str]) -> None:
    STATUS_PATH.write_text(json.dumps({"live_agent_integration": status, "reason": reason,
                                       "evidence_files": files, "checked_at_utc": _now()}, indent=2))


def main() -> None:
    ap = argparse.ArgumentParser(description=__doc__)
    ap.add_argument("--mock", action="store_true", help="record scripted-mock traces (does not prove live integration)")
    args = ap.parse_args()
    TRACE_DIR.mkdir(parents=True, exist_ok=True)
    prior = json.loads(STATUS_PATH.read_text())["live_agent_integration"] if STATUS_PATH.exists() else None

    if not args.mock and config.openai_api_key() is None:
        _write_status("unverified", "OPENAI_API_KEY not set; no live LLM-to-MCP trace could be recorded. "
                      "Only mocked LLM tests have run.", [])
        print("LIVE AGENT INTEGRATION REMAINS UNVERIFIED: OPENAI_API_KEY is not set.")
        sys.exit(2)

    prefix = "mock" if args.mock else "live"
    files, all_met = [], True
    for scenario in SCENARIOS:
        data = asyncio.run(record(scenario, args.mock))
        path = TRACE_DIR / f"{prefix}_{scenario}_trace.json"
        path.write_text(json.dumps(data, indent=2))
        files.append(str(path.relative_to(config.ROOT)))
        all_met &= data["criteria_met"]
        print(f"[{prefix}] {scenario}: criteria_met={data['criteria_met']} {data['criteria']} -> {path.name}")

    if args.mock:
        if prior != "verified":
            _write_status("unverified", "Only scripted-mock traces recorded; live agent integration remains unverified.", [])
        print("Mock traces recorded. LIVE AGENT INTEGRATION REMAINS UNVERIFIED unless a live run passes.")
    elif all_met:
        _write_status("verified", "Live success and failed-tool-recovery traces met all criteria.", files)
        print("Live agent integration VERIFIED (see traces).")
    else:
        _write_status("unverified", "Live run executed but at least one trace criterion was not met; inspect traces.", files)
        print("Live run did NOT meet all criteria; live agent integration remains unverified.")
        sys.exit(1)


if __name__ == "__main__":
    main()
