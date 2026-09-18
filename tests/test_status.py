from campaignguard.schemas import Finding, RequiredCheck
from campaignguard.status import REQUIRED_CHECK_NAMES, decide_review_status

F = Finding(issue_type="missing_disclosure", severity="medium", source_ids=["POL-004"], explanation="x",
            detected_by="deterministic")


def checks(**overrides):
    return [RequiredCheck(name=n, state=overrides.get(n, "complete")) for n in REQUIRED_CHECK_NAMES]


def test_incomplete_required_check_takes_precedence_over_findings():
    for name in REQUIRED_CHECK_NAMES:
        for state in ("incomplete", "not_run"):
            assert decide_review_status(checks(**{name: state}), [F]) == "needs_review"
            assert decide_review_status(checks(**{name: state}), []) == "needs_review"


def test_complete_review_with_findings_is_issues_found():
    assert decide_review_status(checks(), [F]) == "issues_found"


def test_only_complete_review_without_findings_is_no_issues_detected():
    assert decide_review_status(checks(), []) == "no_issues_detected"


def test_missing_required_check_entry_is_needs_review():
    assert decide_review_status(checks()[:-1], []) == "needs_review"
