from datetime import date

import pytest

from campaignguard.data import products
from campaignguard.revision_check import verify_revision

D = date(2026, 9, 15)
P = products()


def check(text, pid):
    return verify_revision(text, P.get(pid), D)


@pytest.mark.parametrize("text,pid", [
    ("Earn a $500 statement credit after you spend $6,000 in purchases in the first 3 months.", "NBP-CARD-300"),
    ("Annual fee is $0 the first year, then $95.", "NBP-CARD-300"),
    ("The $15 monthly fee is waived with a $2,500 minimum daily balance.", "NBP-CHK-100"),
    ("Offer ends October 31, 2026.", "NBP-CHK-100"),
    ("No annual fee the first year, then $95.", "NBP-CARD-300"),
])
def test_supported_revisions_verify(text, pid):
    rc = check(text, pid)
    assert rc.status == "verified" and not rc.needs_human_review


@pytest.mark.parametrize("text,pid,bad", [
    ("Earn a $750 bonus.", "NBP-CHK-100", "$750"),
    ("The $15 annual fee applies.", "NBP-CHK-100", "$15"),
    ("Enjoy no monthly fees.", "NBP-CHK-100", "no monthly fees"),
    ("You will never pay an annual fee.", "NBP-CARD-300", "never pay an annual fee"),
    ("Your credit posts instantly.", "NBP-CARD-300", "instantly"),
    ("Offer ends December 31, 2026.", "NBP-CHK-100", "December 31, 2026"),
    ("Deposit $5,000 within 45 days.", "NBP-CHK-100", "45 days"),
    ("Foreign transaction fee: 5%.", "NBP-CARD-300", "5%"),
    ("Earn a $150 bonus.", "NBP-SAV-200", "offer"),  # offer expired on review date
])
def test_contradicting_claims_are_unsupported_and_flagged(text, pid, bad):
    rc = check(text, pid)
    assert rc.status == "unsupported" and rc.needs_human_review
    assert bad in {c.text for c in rc.claims if c.result == "unsupported"}


def test_unverifiable_cases_are_flagged():
    assert check("Get $300.", "NBP-UNKNOWN").status == "unverifiable"
    assert check("Fees are waived.", "NBP-CHK-100").status == "unverifiable"  # no matchable condition
    assert check("Offer ends October 31.", "NBP-CHK-100").status == "unverifiable"  # no year
    assert all(r.needs_human_review for r in (check("Get $300.", "NBP-UNKNOWN"),))


def test_no_checkable_claims_is_not_semantic_verification():
    rc = check('Add: "Subject to credit approval."', "NBP-CARD-300")
    assert rc.status == "no_checkable_claims" and not rc.needs_human_review
    assert "not semantically verified" in rc.scope_note
    assert verify_revision(None, P["NBP-CARD-300"], D) is None


def test_deterministic_auto_revision_that_keeps_another_violation_is_flagged():
    # C30 auto-fix replaces the amount but the sentence still says "no annual fee".
    rc = check("$500 bonus and no annual fee", "NBP-CARD-300")
    assert rc.status == "unsupported"
    assert [c.text for c in rc.claims if c.result == "unsupported"] == ["no annual fee"]
