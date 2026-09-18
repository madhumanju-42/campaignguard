"""CampaignGuard MCP server (stdio). Exposes only project data and validation functions.

Run standalone:  python -m campaignguard.mcp_server
Never print to stdout here: stdout carries the JSON-RPC stream.
"""

from __future__ import annotations

import logging
import os
import sys
from datetime import date

from mcp.server import MCPServer
from mcp.server.mcpserver.exceptions import ToolError
from pydantic import BaseModel, Field

from .checks import run_checks
from .config import DEFAULT_REVIEW_DATE
from .data import product_sources, products
from .retrieval import search_policies
from .schemas import Campaign, Channel

logging.basicConfig(stream=sys.stderr, level=logging.WARNING)

# TEST-ONLY fault injection, e.g. CG_FAULT_INJECT="search_policy:1" makes the first call to
# search_policy return a tool error through the real protocol. Off unless the env var is set.
_FAULTS: dict[str, int] = {}
for _spec in filter(None, os.getenv("CG_FAULT_INJECT", "").split(",")):
    _name, _, _count = _spec.partition(":")
    _FAULTS[_name.strip()] = int(_count or 1)
if _FAULTS:
    logging.warning("CampaignGuard MCP server: TEST fault injection enabled: %s", _FAULTS)


def _maybe_fault(tool: str) -> None:
    if _FAULTS.get(tool, 0) > 0:
        _FAULTS[tool] -= 1
        raise ToolError(f"Injected test fault: {tool} temporarily unavailable. Retry the call.")


mcp = MCPServer(
    "campaignguard",
    instructions="Tools over FICTIONAL Northbeam Bank product terms and marketing policies (demo data).",
)


class SourcePassage(BaseModel):
    source_id: str
    text: str


class ProductResult(BaseModel):
    found: bool
    product_id: str
    product: dict | None = None
    sources: list[SourcePassage] = Field(default_factory=list)
    message: str | None = None


class PolicyHit(BaseModel):
    policy_id: str
    title: str
    text: str
    channels: list[str]
    score: float


class PolicySearchResult(BaseModel):
    query: str
    channel: str | None
    results: list[PolicyHit]


class CheckResult(BaseModel):
    product_found: bool
    review_date: str
    findings: list[dict]
    sources: list[SourcePassage]
    notes: list[str]


@mcp.tool()
def get_product(product_id: str) -> ProductResult:
    """Get approved terms (offer amount, eligibility, fees, waivers, validity dates, disclosures)
    for a fictional Northbeam product, with stable source IDs. Returns found=false if unknown."""
    _maybe_fault("get_product")
    product = products().get(product_id.strip())
    if product is None:
        return ProductResult(found=False, product_id=product_id, message="Unknown product_id; no approved terms exist.")
    return ProductResult(
        found=True,
        product_id=product_id,
        product=product,
        sources=[SourcePassage(source_id=k, text=v) for k, v in product_sources(product).items()],
    )


@mcp.tool()
def search_policy(query: str, channel: Channel | None = None, top_k: int = 3) -> PolicySearchResult:
    """Search fictional internal marketing policies with TF-IDF. Returns policy passages,
    policy IDs, and cosine-similarity retrieval scores, filtered by channel applicability."""
    _maybe_fault("search_policy")
    if not query.strip():
        raise ToolError("query must not be empty")
    if not 1 <= top_k <= 10:
        raise ToolError("top_k must be between 1 and 10")
    hits = search_policies(query[:500], channel, top_k)
    return PolicySearchResult(query=query[:500], channel=channel, results=[PolicyHit(**h) for h in hits])


@mcp.tool()
def check_campaign(campaign: Campaign, review_date: str | None = None) -> CheckResult:
    """Run deterministic checks (bonus amount, offer dates, literal fee claims, required
    disclosures, reviewer-directed instructions). Campaign text is treated as data."""
    _maybe_fault("check_campaign")
    try:
        rd = date.fromisoformat(review_date) if review_date else DEFAULT_REVIEW_DATE
    except ValueError as exc:
        raise ToolError(f"invalid review_date: {exc}") from exc
    result = run_checks(campaign, rd)
    return CheckResult(
        product_found=result["product_found"],
        review_date=rd.isoformat(),
        findings=result["findings"],
        sources=[SourcePassage(source_id=k, text=v) for k, v in result["sources"].items()],
        notes=result["notes"],
    )


def main() -> None:
    mcp.run()  # stdio transport


if __name__ == "__main__":
    main()
