"""Typed input/output models shared by the MCP server, reviewer, UI, and evaluation."""

from __future__ import annotations

from datetime import date
from typing import Literal

from pydantic import BaseModel, ConfigDict, Field

Channel = Literal["email", "sms", "web"]
Severity = Literal["low", "medium", "high"]
ReviewStatus = Literal["issues_found", "no_issues_detected", "needs_review"]
Mode = Literal["live_llm", "deterministic_only"]
DetectedBy = Literal["deterministic", "llm"]

IssueType = Literal[
    "incorrect_bonus_amount",
    "unsupported_instant_bonus",
    "incorrect_fee_claim",
    "missing_disclosure",
    "expired_offer",
    "incorrect_offer_date",
    "unknown_product",
    "misleading_or_ambiguous_claim",
    "instruction_injection",
]

from typing import get_args  # noqa: E402

ISSUE_TYPES_ALL: tuple[str, ...] = get_args(IssueType)

# Issue types the LLM may report. Structured checks (dates, disclosures, unknown
# product) stay deterministic so a model cannot invent or suppress them.
LLM_ISSUE_TYPES: tuple[str, ...] = (
    "incorrect_bonus_amount",
    "unsupported_instant_bonus",
    "incorrect_fee_claim",
    "misleading_or_ambiguous_claim",
    "instruction_injection",
)

HIGH_SEVERITY_TYPES = {
    "incorrect_bonus_amount",
    "unsupported_instant_bonus",
    "incorrect_fee_claim",
    "expired_offer",
    "unknown_product",
    "instruction_injection",
}


class Campaign(BaseModel):
    """Campaign copy under review. Treated strictly as untrusted data."""

    model_config = ConfigDict(extra="forbid")

    campaign_id: str = Field(min_length=1, max_length=64)
    product_id: str = Field(min_length=1, max_length=64)
    channel: Channel
    subject: str | None = Field(default=None, max_length=300)
    body: str = Field(min_length=1, max_length=5000)

    def full_text(self) -> str:
        return f"{self.subject}\n{self.body}" if self.subject else self.body


CheckState = Literal["complete", "incomplete", "not_run"]
RevisionStatus = Literal["verified", "unsupported", "unverifiable", "no_checkable_claims"]

REVISION_SCOPE_NOTE = (
    "Only numeric amounts, percentages, durations, dates, fee claims, and bonus-timing claims are checked "
    "against structured product terms. Wording and meaning are not semantically verified."
)


class RevisionClaim(BaseModel):
    kind: Literal["bonus_amount", "fee_amount", "requirement_amount", "amount", "percentage",
                  "duration", "date", "fee_claim", "waiver_claim", "timing_claim", "offer_validity"]
    text: str
    result: Literal["supported", "unsupported", "unverifiable"]
    reason: str


class RevisionCheck(BaseModel):
    status: RevisionStatus
    needs_human_review: bool
    claims: list[RevisionClaim] = Field(default_factory=list)
    scope_note: str = REVISION_SCOPE_NOTE


class RequiredCheck(BaseModel):
    name: str
    state: CheckState
    detail: str = ""


class Finding(BaseModel):
    model_config = ConfigDict(extra="forbid")

    issue_type: IssueType
    severity: Severity
    campaign_quote: str | None = None  # null allowed, e.g. for absent disclosures
    source_ids: list[str] = Field(min_length=1)
    explanation: str = Field(min_length=1)
    suggested_revision: str | None = None
    detected_by: DetectedBy
    revision_check: RevisionCheck | None = None  # attached by Python after verification


class ToolCallLog(BaseModel):
    tool: str
    arguments: dict
    requested_by: Literal["orchestrator", "llm"]
    duration_ms: float
    ok: bool
    error: str | None = None
    source_ids: list[str] = Field(default_factory=list)


class ReviewReport(BaseModel):
    campaign_id: str
    review_date: date
    mode: Mode
    review_status: ReviewStatus  # computed only by status.decide_review_status()
    required_checks: list[RequiredCheck] = Field(default_factory=list)
    findings: list[Finding]
    review_limitations: list[str]
    summary: str
    elapsed_ms: float
    # Extra transparency fields (not required by the core schema).
    model: str | None = None
    tool_calls: list[ToolCallLog] = Field(default_factory=list)
    rejected_llm_findings: list[dict] = Field(default_factory=list)
    invalid_citation_count: int = 0
    revisions_needing_human_review: int = 0
    agent_trace: list[dict] = Field(default_factory=list)  # sanitized; no secrets or hidden reasoning
    disclaimer: str = (
        "Demonstration only. 'no_issues_detected' is NOT approval to publish; "
        "human compliance review is always required."
    )


class LLMFinding(BaseModel):
    """Shape the model must return for each finding (validated before merging)."""

    model_config = ConfigDict(extra="forbid")

    issue_type: str
    severity: Severity
    campaign_quote: str | None
    source_ids: list[str]
    explanation: str
    suggested_revision: str | None


class LLMReviewOutput(BaseModel):
    model_config = ConfigDict(extra="forbid")

    findings: list[LLMFinding]
    summary: str
