"""Configuration for the expired-domain hunter."""

from __future__ import annotations

import os
from pathlib import Path

BASE_DIR = Path(__file__).resolve().parent
DEFAULT_INPUT_PATH = BASE_DIR / "input" / "domains.csv"
DEFAULT_OUTPUT_DIR = BASE_DIR / "output"

# Strict investor-oriented 0–10 score model. These are component maxima, not
# percentages and not a rescaled copy of the previous 100-point presentation.
SCORING_WEIGHTS = {
    "natural_language": 2.0,
    "brandability": 2.0,
    "commercial_demand": 2.0,
    "shortness_memorability": 1.0,
    "keyword_quality": 1.0,
    "resale_potential": 1.0,
    "broad_clean_market_appeal": 1.0,
}

FINAL_SCORE_THRESHOLD = 7.0
BUY_THRESHOLD = FINAL_SCORE_THRESHOLD
WATCH_THRESHOLD = 6.0

# Bounded 0–10 penalties. They prevent one keyword from inflating an
# unnatural, risky, or investor-unfriendly name.
PENALTIES = {
    "numbers": 0.5,
    "hyphens": 0.7,
    "awkward_spelling": 0.8,
    "suspicious_history": 1.2,
    "spam": 2.0,
    "trademark_risk": 1.5,
    "weak_commercial_potential": 0.8,
    "generic_suffix": 0.8,
    "keyword_stuffing": 1.0,
    "invented_string": 1.0,
    "long_three_word": 0.8,
    "personal_name": 0.6,
    "narrow_niche": 0.5,
}
MAX_PENALTY = 5.5

# Public service safeguards.
WAYBACK_ENDPOINT = "https://web.archive.org/cdx/search/cdx"
WAYBACK_TIMEOUT_SECONDS = float(os.getenv("WAYBACK_TIMEOUT_SECONDS", "15"))
WAYBACK_MAX_REQUESTS = int(os.getenv("WAYBACK_MAX_REQUESTS", "25"))
WAYBACK_RETRIES = int(os.getenv("WAYBACK_RETRIES", "2"))
WAYBACK_USER_AGENT = "expired-domain-hunter/1.0 (+https://github.com/Ensscience/expired-domain-hunter)"

AVAILABILITY_MAX_REQUESTS = int(os.getenv("AVAILABILITY_MAX_REQUESTS", "100"))
AVAILABILITY_TIMEOUT_SECONDS = float(os.getenv("AVAILABILITY_TIMEOUT_SECONDS", "5"))
AVAILABILITY_RETRIES = int(os.getenv("AVAILABILITY_RETRIES", "0"))
DEFAULT_STATE_PATH = BASE_DIR / ".state" / "processed_domains.json"

# Conservative heuristic valuation only; never a purchase guarantee.
BID_VALUE_RATIO = 0.02
MIN_MAX_BID = 5.0
MAX_MAX_BID = 250.0

DEFAULT_TOP_N = int(os.getenv("TOP_N", "50"))
TELEGRAM_MAX_ITEMS = int(os.getenv("TELEGRAM_MAX_ITEMS", "50"))


def classification(score: float) -> str:
    """Return the strict 0–10 investor classification."""

    value = float(score)
    if value >= 9.0:
        return "EXCEPTIONAL"
    if value >= 8.0:
        return "STRONG"
    if value >= 7.0:
        return "GOOD"
    if value >= 6.0:
        return "DECENT"
    if value >= 5.0:
        return "WEAK"
    return "IGNORE"
