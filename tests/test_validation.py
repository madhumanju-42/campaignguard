from campaignguard.data import all_source_ids
from campaignguard.schemas import Finding, LLMFinding, ReviewReport
from campaignguard.validation import check_report_invariants, merge_findings, quote_in_campaign, validate_llm_findings

from .conftest import REVIEW_DATE


def llm_finding(**kw):
    base = dict(issue_type="unsupported_instant_bonus", severity="high", campaign_quote="bonus instantly",
                source_ids=["NBP-CARD-300:offer", "POL-002"], explanation="x", suggested_revision=None)
    return LLMFinding(**{**base, **kw})


AVAILABLE = {"NBP-CARD-300:offer", "NBP-CARD-300:fees", "POL-002"}


def test_valid_llm_finding_accepted(card_instant):
    acc, rej, bad = validate_llm_findings([llm_finding()], card_instant, AVAILABLE, all_source_ids())
    assert len(acc) == 1 and not rej and bad == 0 and acc[0].detected_by == "llm"


def test_unknown_and_unretrieved_citations_rejected(card_instant):
    f1 = llm_finding(source_ids=["POL-999"])            # does not exist
    f2 = llm_finding(source_ids=["POL-007"])            # exists but was not retrieved in this review
    acc, rej, bad = validate_llm_findings([f1, f2], card_instant, AVAILABLE, all_source_ids())
    assert acc == [] and len(rej) == 2 and bad == 2


def test_unsupported_quote_rejected(card_instant):
    acc, rej, _ = validate_llm_findings([llm_finding(campaign_quote="guaranteed approval")], card_instant,
                                        AVAILABLE, all_source_ids())
    assert acc == [] and "quote does not occur" in rej[0]["reasons"][0]


def test_quote_matching_is_whitespace_and_case_insensitive(card_instant):
    assert quote_in_campaign("GET your   $500 bonus", card_instant)


def test_merge_preserves_deterministic_and_dedupes():
    det = Finding(issue_type="incorrect_fee_claim", severity="high", campaign_quote="no annual fee",
                  source_ids=["POL-003"], explanation="d", detected_by="deterministic")
    dup = det.model_copy(update={"detected_by": "llm", "campaign_quote": "pay no annual fee"})
    new = det.model_copy(update={"detected_by": "llm", "issue_type": "misleading_or_ambiguous_claim"})
    merged = merge_findings([det], [dup, new])
    assert merged[0] == det and len(merged) == 2


def test_invariants_catch_lost_deterministic_finding_and_unverified_revision(card_instant):
    det = Finding(issue_type="missing_disclosure", severity="medium", source_ids=["POL-004"], explanation="d",
                  detected_by="deterministic")
    unverified = Finding(issue_type="incorrect_fee_claim", severity="high", source_ids=["POL-004"], explanation="x",
                         suggested_revision="No fees ever.", detected_by="llm")
    rep = ReviewReport(campaign_id="x", review_date=REVIEW_DATE, mode="live_llm", review_status="needs_review",
                       findings=[unverified], review_limitations=[], summary="", elapsed_ms=1)
    problems = check_report_invariants(rep, card_instant, {"POL-004"}, [det])
    assert any("deterministic finding lost" in p for p in problems)
    assert any("not verified" in p for p in problems)
