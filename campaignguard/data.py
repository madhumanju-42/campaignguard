"""Loaders for runtime reference data. Evaluation labels are deliberately NOT loaded here."""

from __future__ import annotations

import json
from functools import lru_cache
from pathlib import Path

from .config import DATA_DIR
from .schemas import Campaign


@lru_cache
def _load(name: str) -> dict:
    return json.loads((DATA_DIR / name).read_text())


def products() -> dict[str, dict]:
    return {p["product_id"]: p for p in _load("products.json")["products"]}


def disclosures() -> dict[str, dict]:
    return {d["disclosure_id"]: d for d in _load("products.json")["disclosures"]}


def policies() -> dict[str, dict]:
    return {p["policy_id"]: p for p in _load("policies.json")["policies"]}


def campaigns() -> list[Campaign]:
    return [Campaign(**c) for c in _load("campaigns.json")["campaigns"]]


def dataset_version() -> str:
    return _load("products.json")["dataset_version"]


def product_sources(product: dict) -> dict[str, str]:
    """Stable source references for a product's terms, keyed by source ID."""
    pid = product["product_id"]
    offer = product["offer"]
    fees = "; ".join(
        f"{f['name']}: " + (f"${f['amount']}" if "amount" in f else f"{f['amount_percent']}%")
        + (f" ({f['note']})" if f.get("note") else "")
        for f in product["fees"]
    )
    src = {
        f"{pid}:offer": (
            f"{product['name']} offer: ${offer['bonus_amount']} {offer['bonus_form']}. "
            f"Valid {offer['valid_from']} through {offer['valid_through']}. {offer['bonus_timing']}"
        ),
        f"{pid}:eligibility": "Eligibility: " + "; ".join(product["eligibility"]),
        f"{pid}:fees": f"Fees: {fees}. Waivers: " + "; ".join(product["fee_waivers"]),
    }
    discs = disclosures()
    for ids in product["required_disclosures"].values():
        for did in ids:
            src[did] = discs[did]["text"]
    return src


def all_source_ids() -> set[str]:
    ids = set(policies()) | set(disclosures())
    for p in products().values():
        ids |= set(product_sources(p))
    return ids


def campaigns_from_csv(path_or_buffer) -> list[Campaign]:
    import pandas as pd

    df = pd.read_csv(path_or_buffer, dtype=str).fillna("")
    required = {"campaign_id", "product_id", "channel", "body"}
    missing = required - set(df.columns)
    if missing:
        raise ValueError(f"CSV missing columns: {sorted(missing)}")
    out = []
    for row in df.to_dict("records"):
        out.append(
            Campaign(
                campaign_id=row["campaign_id"].strip(),
                product_id=row["product_id"].strip(),
                channel=row["channel"].strip().lower(),
                subject=(row.get("subject") or "").strip() or None,
                body=row["body"],
            )
        )
    return out


def read_json(path: Path) -> dict:
    return json.loads(Path(path).read_text())
