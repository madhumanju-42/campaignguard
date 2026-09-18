"""Verify suggested revisions against STRUCTURED product terms.

What is checked (pattern-based, English, digits only):
  - dollar amounts, classified by nearby keywords as bonus / fee / requirement amounts
  - percentages, durations ("30 days", "90-day"), and dates
  - "no fee" / "never pay a fee" style claims, fee-waiver claims, and "instant" bonus-timing claims
  - promoting an offer that is outside its validity window on the review date

What is NOT checked: meaning, tone, completeness, amounts or numbers written in words,
non-numeric promises. A 'verified' result only means the extracted claims match product
terms; it is not semantic verification. Unsupported or unverifiable revisions are flagged
for human review.
"""

from __future__ import annotations

import re
from datetime import date, datetime

from .checks import AMOUNT_RE, BONUS_WORDS, FEE_CLAIM_RE, MONTHS, classify_amount
from .schemas import RevisionCheck, RevisionClaim

FEE_WORDS = ("fee",)
REQ_WORDS = ("deposit", "spend", "purchase", "balance", "maintain", "minimum", "debit card")
AMOUNT_GROUPS = ((BONUS_WORDS + ("credit",), "bonus"), (FEE_WORDS, "fee"), (REQ_WORDS, "requirement"))

PERCENT_RE = re.compile(r"(\d+(?:\.\d+)?)\s?%")
DURATION_RE = re.compile(r"\b(\d+)[- ](day|week|month|year)s?\b", re.I)
DATE_RE = re.compile(rf"\b((?:{MONTHS})\s+\d{{1,2}}(?:,\s*\d{{4}})?|\d{{4}}-\d{{2}}-\d{{2}})\b", re.I)
NO_FEE_EXTRA_RE = re.compile(r"\b(never (pay|be charged) (an? |any )?(\w+ )?fees?|without (any )?fees?|fees? (are|is) waived for life)\b", re.I)
WAIVER_RE = re.compile(r"\bwaive[sd]?\b|\bwaiver\b", re.I)
INSTANT_RE = re.compile(r"\b(instant(ly)?|immediate(ly)?|same[- ]day|right away|the moment|no waiting|at (account )?opening)\b", re.I)


def _money(tok: str) -> int:
    return int(re.sub(r"[^\d]", "", tok.split(".")[0]) or 0)


def _sentence(text: str, start: int, end: int) -> str:
    left = max(text.rfind(".", 0, start), text.rfind(";", 0, start), text.rfind("\n", 0, start)) + 1
    ends = [i for i in (text.find(".", end), text.find(";", end), text.find("\n", end)) if i != -1]
    return text[left:(min(ends) if ends else len(text))].lower()


def product_facts(product: dict) -> dict:
    texts = product["eligibility"] + product["fee_waivers"] + [product["offer"]["bonus_timing"]]
    texts += [f.get("note", "") for f in product["fees"]]
    blob = " ".join(texts)
    fee_amounts: dict[int, set[str]] = {}
    for f in product["fees"]:
        if "amount" in f:
            fee_amounts.setdefault(int(f["amount"]), set()).add(f["frequency"])
        if "$0" in f.get("note", ""):
            fee_amounts.setdefault(0, set()).add(f["frequency"])
    return {
        "bonus": int(product["offer"]["bonus_amount"]),
        "fees": fee_amounts,
        "requirement_amounts": {_money(m.group(0)) for m in AMOUNT_RE.finditer(" ".join(product["eligibility"] + product["fee_waivers"]))},
        "waiver_amounts": {_money(m.group(0)) for m in AMOUNT_RE.finditer(" ".join(product["fee_waivers"]))},
        "percents": {float(f["amount_percent"]) for f in product["fees"] if "amount_percent" in f},
        "durations": {(int(n), u.lower()) for n, u in DURATION_RE.findall(blob)},
        "dates": {date.fromisoformat(product["offer"]["valid_from"]), date.fromisoformat(product["offer"]["valid_through"])},
        "valid_from": date.fromisoformat(product["offer"]["valid_from"]),
        "valid_through": date.fromisoformat(product["offer"]["valid_through"]),
        "has_fees": bool(product["fees"]),
        "first_year_waiver": any("first year" in w.lower() for w in texts),
        "instant_timing": "instant" in product["offer"]["bonus_timing"].lower(),
    }


def verify_revision(revision: str | None, product: dict | None, review_date: date) -> RevisionCheck | None:
    if not revision:
        return None
    claims: list[RevisionClaim] = []

    def add(kind, text, result, reason):
        claims.append(RevisionClaim(kind=kind, text=text, result=result, reason=reason))

    # ---------- extract claims ----------
    amounts = [(m, _money(m.group(0))) for m in AMOUNT_RE.finditer(revision)]
    percents = list(PERCENT_RE.finditer(revision))
    durations = list(DURATION_RE.finditer(revision))
    dates = list(DATE_RE.finditer(revision))
    no_fee = list(FEE_CLAIM_RE.finditer(revision)) + list(NO_FEE_EXTRA_RE.finditer(revision))
    waivers = list(WAIVER_RE.finditer(revision))
    instant = list(INSTANT_RE.finditer(revision))

    if product is None:
        for m in [a[0] for a in amounts] + percents + durations + dates + no_fee + waivers + instant:
            add("amount", m.group(0), "unverifiable", "No structured product terms exist for this product.")
        return _finish(claims)

    facts = product_facts(product)

    for m, value in amounts:
        role = classify_amount(revision, m.start(), m.end(), AMOUNT_GROUPS)
        sent = _sentence(revision, m.start(), m.end())
        if role == "bonus":
            ok = value == facts["bonus"]
            add("bonus_amount", m.group(0), "supported" if ok else "unsupported",
                f"Approved bonus is ${facts['bonus']:,}." if not ok else "Matches approved bonus amount.")
        elif role == "fee":
            freqs = facts["fees"].get(value)
            if freqs is None:
                add("fee_amount", m.group(0), "unsupported", f"No product fee of ${value:,}.")
            else:
                stated = {f for f in ("monthly", "annual") if f in sent}
                if stated and not (stated & freqs):
                    add("fee_amount", m.group(0), "unsupported", f"${value:,} fee is {sorted(freqs)}, not {sorted(stated)}.")
                else:
                    add("fee_amount", m.group(0), "supported", "Matches a product fee.")
        elif role == "requirement":
            ok = value in facts["requirement_amounts"]
            add("requirement_amount", m.group(0), "supported" if ok else "unsupported",
                "Matches an eligibility or waiver amount." if ok else f"${value:,} is not an eligibility or waiver amount.")
        else:
            add("amount", m.group(0), "unverifiable", "Could not determine what this amount refers to.")

    for m in percents:
        ok = float(m.group(1)) in facts["percents"]
        add("percentage", m.group(0), "supported" if ok else "unsupported",
            "Matches a product percentage." if ok else "Percentage not in product terms.")

    for m in durations:
        key = (int(m.group(1)), m.group(2).lower())
        ok = key in facts["durations"]
        add("duration", m.group(0), "supported" if ok else "unsupported",
            "Matches a duration in product terms." if ok else "Duration not in product terms.")

    for m in dates:
        raw = m.group(1)
        parsed = None
        for fmt in ("%B %d, %Y", "%B %d,%Y", "%Y-%m-%d"):
            try:
                parsed = datetime.strptime(raw, fmt).date()
                break
            except ValueError:
                continue
        if parsed is None:
            add("date", raw, "unverifiable", "Date has no year; cannot be matched exactly.")
        elif parsed in facts["dates"]:
            add("date", raw, "supported", "Matches the offer validity dates.")
        else:
            add("date", raw, "unsupported", f"Offer runs {facts['valid_from']} to {facts['valid_through']}.")

    for m in no_fee:
        sent = _sentence(revision, m.start(), m.end())
        if not facts["has_fees"]:
            add("fee_claim", m.group(0), "supported", "Product has no fees.")
        elif "first year" in sent and facts["first_year_waiver"]:
            add("fee_claim", m.group(0), "supported", "Product terms waive the fee for the first year only, and the revision says so.")
        else:
            add("fee_claim", m.group(0), "unsupported", "Product has fees; a no-fee claim contradicts the terms.")

    for m in waivers:
        sent = _sentence(revision, m.start(), m.end())
        sent_amounts = {_money(a.group(0)) for a in AMOUNT_RE.finditer(sent)}
        if (sent_amounts & facts["waiver_amounts"]) or ("first year" in sent and facts["first_year_waiver"]):
            add("waiver_claim", m.group(0), "supported", "Waiver condition matches product terms.")
        else:
            add("waiver_claim", m.group(0), "unverifiable", "Waiver mentioned without a condition that can be matched to product terms.")

    for m in instant:
        if facts["instant_timing"]:
            add("timing_claim", m.group(0), "supported", "Product terms state instant timing.")
        else:
            add("timing_claim", m.group(0), "unsupported", "Product terms: " + product["offer"]["bonus_timing"])

    promotes_offer = any(c.kind in ("bonus_amount", "date") for c in claims)
    if promotes_offer and not (facts["valid_from"] <= review_date <= facts["valid_through"]):
        add("offer_validity", "offer", "unsupported",
            f"Offer is not valid on review date {review_date} (window {facts['valid_from']} to {facts['valid_through']}).")

    return _finish(claims)


def _finish(claims: list[RevisionClaim]) -> RevisionCheck:
    results = {c.result for c in claims}
    if not claims:
        status = "no_checkable_claims"
    elif "unsupported" in results:
        status = "unsupported"
    elif "unverifiable" in results:
        status = "unverifiable"
    else:
        status = "verified"
    return RevisionCheck(status=status, needs_human_review=status in ("unsupported", "unverifiable"), claims=claims)
