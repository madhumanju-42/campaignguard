"""Deterministic checks for structured, verifiable mismatches.

Limits (documented on purpose): these are regex/text-matching rules. They catch
literal patterns ("no monthly fees", "$750 statement credit", a missing "Reply STOP")
but not paraphrases ("you'll never pay an annual fee"), amounts written in words,
disclosures that are reworded, or meaning that depends on context. Semantic claims
are left to the LLM reviewer; a clean deterministic result is NOT proof of correctness.
"""

from __future__ import annotations

import re
from datetime import date, datetime

from .data import disclosures, policies, product_sources, products
from .schemas import Campaign, Finding

AMOUNT_RE = re.compile(r"\$\s?(\d{1,3}(?:,\d{3})+|\d+)(?:\.\d{2})?")
BONUS_WORDS = ("bonus", "statement credit", "cash back", "cash", "reward", "earn", "get", "receive")
REQUIREMENT_WORDS = ("deposit", "spend", "purchase", "balance", "fee", "maintain", "minimum", "draw", "with $")
FEE_CLAIM_RE = re.compile(
    r"\b(no (monthly |maintenance |account |annual )?fees?|fee[- ]free|free (business )?checking|zero fees)\b",
    re.IGNORECASE,
)
INJECTION_RE = re.compile(
    r"(ignore|disregard)\s+(all\s+|any\s+|the\s+)?(prior\s+|previous\s+)?(review\s+)?(policies|instructions|rules)"
    r"|mark\s+(this|the)\s+campaign\s+as\s+(approved|compliant)"
    r"|report\s+no\s+issues",
    re.IGNORECASE,
)
MONTHS = "January|February|March|April|May|June|July|August|September|October|November|December"
END_DATE_RE = re.compile(
    rf"\b(?:ends?|expires?|through|until|by)\s+((?:{MONTHS})\s+\d{{1,2}},\s+\d{{4}}|\d{{4}}-\d{{2}}-\d{{2}})",
    re.IGNORECASE,
)


def _sentence_around(text: str, start: int, end: int) -> str:
    dot, nl = text.rfind(". ", 0, start), text.rfind("\n", 0, start)
    left = max(dot + 2 if dot != -1 else 0, nl + 1 if nl != -1 else 0)
    ends = [i for i in (text.find(". ", end), text.find("\n", end)) if i != -1]
    right = min(ends) + 1 if ends else len(text)
    return text[left:right].strip()


def classify_amount(text: str, start: int, end: int,
                    groups: tuple[tuple[tuple[str, ...], str], ...] | None = None) -> str | None:
    """Label a dollar amount by the nearest keyword group in the same sentence."""
    groups = groups or ((BONUS_WORDS, "bonus"), (REQUIREMENT_WORDS, "requirement"))
    lo = text.lower()
    best: tuple[int, str] | None = None
    window_before = lo[max(0, start - 45):start]
    window_after = lo[end:end + 30]
    # Keywords must come from the same sentence as the amount.
    window_before = re.split(r"[.!?;:]\s", window_before)[-1]
    window_after = re.split(r"[.!?;]\s|[.!?;]$", window_after)[0]
    for words, label in groups:
        for w in words:
            i = window_before.rfind(w)
            if i != -1:
                dist = len(window_before) - (i + len(w))
                if best is None or dist < best[0]:
                    best = (dist, label)
            j = window_after.find(w)
            if j != -1 and (best is None or j < best[0]):
                best = (j, label)
    return best[1] if best else None


def _parse_date(s: str) -> date | None:
    for fmt in ("%B %d, %Y", "%Y-%m-%d"):
        try:
            return datetime.strptime(s.strip(), fmt).date()
        except ValueError:
            continue
    return None


def run_checks(campaign: Campaign, review_date: date) -> dict:
    """Return deterministic findings plus the source passages they cite."""
    text = campaign.full_text()
    lo = text.lower()
    findings: list[Finding] = []
    pols = policies()
    cited: dict[str, str] = {}
    notes: list[str] = []

    def cite(*ids: str) -> list[str]:
        for sid in ids:
            if sid in pols:
                cited[sid] = pols[sid]["text"]
        return list(ids)

    # Review-integrity check runs even for unknown products.
    m = INJECTION_RE.search(text)
    if m:
        findings.append(Finding(
            issue_type="instruction_injection", severity="high",
            campaign_quote=m.group(0), source_ids=cite("POL-010"),
            explanation="Campaign text contains instructions aimed at the review process. It was treated as data and escalated.",
            suggested_revision="Remove all reviewer-directed instructions from the campaign copy.",
            detected_by="deterministic",
        ))

    product = products().get(campaign.product_id)
    if product is None:
        notes.append(f"Product '{campaign.product_id}' not found; offer, fee, date, and disclosure checks could not run.")
        findings.append(Finding(
            issue_type="unknown_product", severity="high", campaign_quote=None,
            source_ids=cite("POL-001"),
            explanation=f"No approved product terms exist for product_id '{campaign.product_id}'. Claims cannot be verified.",
            suggested_revision=None, detected_by="deterministic",
        ))
        return {"product_found": False, "findings": [f.model_dump() for f in findings],
                "sources": cited, "notes": notes}

    psrc = product_sources(product)
    pid = product["product_id"]
    offer = product["offer"]

    # 1. Bonus amount
    expected = offer["bonus_amount"]
    for am in AMOUNT_RE.finditer(text):
        value = int(am.group(1).replace(",", ""))
        if classify_amount(text, am.start(), am.end()) == "bonus" and value != expected:
            cited[f"{pid}:offer"] = psrc[f"{pid}:offer"]
            findings.append(Finding(
                issue_type="incorrect_bonus_amount", severity="high",
                campaign_quote=am.group(0), source_ids=cite(f"{pid}:offer", "POL-001"),
                explanation=f"Campaign states ${value:,} but the approved offer is ${expected:,}.",
                suggested_revision=_sentence_around(text, am.start(), am.end()).replace(am.group(0), f"${expected:,}"),
                detected_by="deterministic",
            ))

    # 2. Offer validity window vs explicit review date
    start = date.fromisoformat(offer["valid_from"])
    end = date.fromisoformat(offer["valid_through"])
    if not (start <= review_date <= end):
        cited[f"{pid}:offer"] = psrc[f"{pid}:offer"]
        findings.append(Finding(
            issue_type="expired_offer", severity="high", campaign_quote=None,
            source_ids=cite(f"{pid}:offer", "POL-005"),
            explanation=f"Review date {review_date} is outside the offer window {start} to {end}.",
            suggested_revision=None, detected_by="deterministic",
        ))

    # 3. Stated end dates must match
    for dm in END_DATE_RE.finditer(text):
        stated = _parse_date(dm.group(1))
        if stated and stated != end:
            cited[f"{pid}:offer"] = psrc[f"{pid}:offer"]
            findings.append(Finding(
                issue_type="incorrect_offer_date", severity="medium",
                campaign_quote=dm.group(0), source_ids=cite(f"{pid}:offer", "POL-005"),
                explanation=f"Campaign states an end date of {stated}; approved offer ends {end}.",
                suggested_revision=dm.group(0).replace(dm.group(1), end.strftime("%B %-d, %Y")),
                detected_by="deterministic",
            ))

    # 4. Literal "no fee" claims when fees exist
    if product["fees"]:
        for fm in FEE_CLAIM_RE.finditer(text):
            cited[f"{pid}:fees"] = psrc[f"{pid}:fees"]
            findings.append(Finding(
                issue_type="incorrect_fee_claim", severity="high",
                campaign_quote=fm.group(0), source_ids=cite(f"{pid}:fees", "POL-003"),
                explanation="Campaign claims no fees, but the product has fees. " + psrc[f"{pid}:fees"],
                suggested_revision=None, detected_by="deterministic",
            ))

    # 5. Required disclosures for this channel (pattern presence only)
    discs = disclosures()
    for did in product["required_disclosures"][campaign.channel]:
        if not any(re.search(p, lo) for p in discs[did]["match_patterns"]):
            cited[did] = discs[did]["text"]
            findings.append(Finding(
                issue_type="missing_disclosure", severity="medium", campaign_quote=None,
                source_ids=cite(did, "POL-004"),
                explanation=f"Required {campaign.channel} disclosure {did} was not found in the campaign text.",
                suggested_revision=f"Add: \"{discs[did]['text']}\"", detected_by="deterministic",
            ))

    # Deduplicate repeated matches (e.g., same amount in subject and body).
    seen: set[tuple[str, str | None, tuple[str, ...]]] = set()
    unique: list[Finding] = []
    for f in findings:
        key = (f.issue_type, f.campaign_quote.lower() if f.campaign_quote else None, tuple(f.source_ids))
        if key not in seen:
            seen.add(key)
            unique.append(f)
    findings = unique

    notes.append("Deterministic checks use text patterns; paraphrased claims and reworded disclosures may be missed.")
    return {"product_found": True, "findings": [f.model_dump() for f in findings],
            "sources": cited, "notes": notes}
