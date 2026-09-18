"""Evidence validation for model findings and final-report invariants."""

from __future__ import annotations

import re

from .schemas import LLM_ISSUE_TYPES, Campaign, Finding, LLMFinding, ReviewReport

def normalize(s: str) -> str:
    s = s.replace("’", "'").replace("‘", "'").replace("“", '"').replace("”", '"')
    s = s.replace("—", "-").replace("–", "-")
    return re.sub(r"\s+", " ", s).strip().lower()


def quote_in_campaign(quote: str, campaign: Campaign) -> bool:
    q = normalize(quote).strip(" .\"'")
    return bool(q) and q in normalize(campaign.full_text())


def validate_llm_findings(
    findings: list[LLMFinding],
    campaign: Campaign,
    available_ids: set[str],
    catalog_ids: set[str],
) -> tuple[list[Finding], list[dict], int]:
    """Accept only model findings whose evidence checks out.

    Returns (accepted, rejected, invalid_citation_count). Suggested revisions are verified
    separately by revision_check.verify_revision for every finding.
    """
    accepted: list[Finding] = []
    rejected: list[dict] = []
    invalid_citations = 0
    for f in findings:
        reasons = []
        if f.issue_type not in LLM_ISSUE_TYPES:
            reasons.append(f"issue_type '{f.issue_type}' not allowed for model findings")
        bad = [s for s in f.source_ids if s not in catalog_ids or s not in available_ids]
        if bad:
            invalid_citations += len(bad)
            reasons.append(f"cited sources not available in this review: {bad}")
        if not f.source_ids:
            reasons.append("no source cited")
        if not f.campaign_quote:
            reasons.append("model findings must quote campaign text")
        elif not quote_in_campaign(f.campaign_quote, campaign):
            reasons.append("quote does not occur in the submitted campaign")
        if reasons:
            rejected.append({"finding": f.model_dump(), "reasons": reasons})
            continue
        accepted.append(Finding(
            issue_type=f.issue_type, severity=f.severity, campaign_quote=f.campaign_quote,
            source_ids=f.source_ids, explanation=f.explanation, suggested_revision=f.suggested_revision, detected_by="llm",
        ))
    return accepted, rejected, invalid_citations


def _overlap(a: str | None, b: str | None) -> bool:
    if a is None or b is None:
        return True
    na, nb = normalize(a), normalize(b)
    return na in nb or nb in na


def merge_findings(deterministic: list[Finding], llm: list[Finding]) -> list[Finding]:
    """Deterministic findings are always kept; LLM findings that duplicate them are dropped."""
    merged = list(deterministic)
    for f in llm:
        dup = any(m.issue_type == f.issue_type and _overlap(m.campaign_quote, f.campaign_quote) for m in merged)
        if not dup:
            merged.append(f)
    return merged


def check_report_invariants(report: ReviewReport, campaign: Campaign, available_ids: set[str],
                            deterministic: list[Finding]) -> list[str]:
    """Final Python-side validation of evidence and preservation. Returns violations (empty = valid)."""
    problems = []
    for f in report.findings:
        for s in f.source_ids:
            if s not in available_ids:
                problems.append(f"finding cites unavailable source {s}")
        if f.campaign_quote is not None and not quote_in_campaign(f.campaign_quote, campaign):
            problems.append(f"quote not found in campaign: {f.campaign_quote[:60]}")
        if f.suggested_revision and f.revision_check is None:
            problems.append(f"suggested revision for {f.issue_type} was not verified")
    kept = {(f.issue_type, f.campaign_quote) for f in report.findings}
    for d in deterministic:
        if (d.issue_type, d.campaign_quote) not in kept:
            problems.append(f"deterministic finding lost: {d.issue_type}")
    return problems
