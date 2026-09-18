import sys
from datetime import date
from pathlib import Path

import pytest

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))

from campaignguard.schemas import Campaign  # noqa: E402

REVIEW_DATE = date(2026, 9, 15)
BT = "Bonus terms apply: northbeam.example/offers."
FEE = "Fees may apply; see fee schedule."
INS = "Deposits insured up to applicable limits."
CR = "Subject to credit approval."


@pytest.fixture
def clean_checking() -> Campaign:
    return Campaign(
        campaign_id="T-CLEAN", product_id="NBP-CHK-100", channel="email", subject="Earn $300",
        body=("Open Business Essentials Checking, deposit $5,000 in new money within 30 days, and keep that "
              f"balance for 60 days to earn a $300 bonus. {BT} {FEE} {INS}"),
    )


@pytest.fixture
def card_instant() -> Campaign:
    return Campaign(
        campaign_id="T-INSTANT", product_id="NBP-CARD-300", channel="email", subject=None,
        body=f"Get your $500 bonus instantly. Spend $6,000 in purchases in the first 3 months. {CR} {BT} {FEE}",
    )
