"""The single place where the final review status is decided. Pure Python, no model input.

Precedence (fixed):
  1. Any required check not 'complete'            -> needs_review
  2. Otherwise, any validated finding             -> issues_found
  3. Otherwise (complete review, zero findings)   -> no_issues_detected

'no_issues_detected' is never approval to publish.
"""

from __future__ import annotations

from .schemas import Finding, RequiredCheck, ReviewStatus

# Checks that must all be complete before a review may report issues_found / no_issues_detected.
REQUIRED_CHECK_NAMES: tuple[str, ...] = (
    "product_terms",            # get_product via MCP returned known product terms
    "deterministic_checks",     # check_campaign via MCP succeeded
    "semantic_llm_review",      # LLM loop finished with schema-valid output (not_run in deterministic_only)
    "model_output_validation",  # every model finding passed citation/quote/type validation
    "report_validation",        # final invariants hold
)


def decide_review_status(checks: list[RequiredCheck], findings: list[Finding]) -> ReviewStatus:
    names = {c.name for c in checks}
    missing = set(REQUIRED_CHECK_NAMES) - names
    if missing or any(c.state != "complete" for c in checks):
        return "needs_review"
    if findings:
        return "issues_found"
    return "no_issues_detected"
