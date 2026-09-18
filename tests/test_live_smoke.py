"""Optional LIVE smoke test. Requires OPENAI_API_KEY (and OPENAI_MODEL). Skipped otherwise."""

import asyncio
import os

import pytest

from campaignguard.data import campaigns
from campaignguard.reviewer import default_provider, review_campaigns

pytestmark = pytest.mark.skipif(not os.getenv("OPENAI_API_KEY"), reason="OPENAI_API_KEY not set; live smoke test skipped")


def test_live_hybrid_review_completes():
    c = next(c for c in campaigns() if c.campaign_id == "C06")
    rep = asyncio.run(review_campaigns([c], provider=default_provider()))[0]
    assert rep.mode == "live_llm"
    assert rep.review_status in {"issues_found", "needs_review", "no_issues_detected"}
    assert any(t.requested_by == "orchestrator" for t in rep.tool_calls)
    print(rep.model_dump_json(indent=2))
