"""Configuration for the expired-domain hunter.

All scoring weights and network limits are intentionally explicit so that the
system can be tuned without rewriting the pipeline.
"""

from __future__ import annotations

import os
from pathlib import Path

BASE_DIR = Path(__file__).resolve().parent
DEFAULT_INPUT_PATH = BASE_DIR / "input" / "domains.csv"
DEFAULT_OUTPUT_DIR = BASE_DIR / "output"

# The score is deliberately transparent and sums to 100.
SCORING_WEIGHTS = {
    "brandability": 20,
    "commercial_intent": 20,
    "keyword_quality": 15,
    "length_readability": 10,
    "historical_quality": 10,
    "backlink_quality": 10,
    "age_history": 5,
    "end_user_potential": 10,
}
BUY_THRESHOLD = 80
WATCH_THRESHOLD = 65

# Penalties are applied after the positive score and are capped in magnitude.
PENALTIES = {
    "numbers": 5,
    "hyphens": 7,
    "awkward_spelling": 6,
    "suspicious_history": 12,
    "spam": 20,
    "trademark_risk": 8,
    "weak_commercial_potential": 8,
}
MAX_PENALTY = 35

# Public service safeguards. The Wayback endpoint is checked only for the
# shortlisted domains, with caching and a modest request budget.
WAYBACK_ENDPOINT = "https://web.archive.org/cdx/search/cdx"
WAYBACK_TIMEOUT_SECONDS = float(os.getenv("WAYBACK_TIMEOUT_SECONDS", "15"))
WAYBACK_MAX_REQUESTS = int(os.getenv("WAYBACK_MAX_REQUESTS", "25"))
WAYBACK_RETRIES = int(os.getenv("WAYBACK_RETRIES", "2"))
WAYBACK_USER_AGENT = "expired-domain-hunter/1.0 (+https://github.com/Ensscience/expired-domain-hunter)"

# Valuation is an AI-assisted heuristic, not a promise of sale price.
BID_VALUE_RATIO = 0.02
MIN_MAX_BID = 5.0
MAX_MAX_BID = 250.0

# The collector is intentionally opt-in. The core pipeline always works from
# CSV so that no single external provider is required.
DEFAULT_TOP_N = int(os.getenv("TOP_N", "50"))
TELEGRAM_MAX_ITEMS = int(os.getenv("TELEGRAM_MAX_ITEMS", "10"))


def classification(score: int) -> str:
    """Return the user-facing classification for a 0-100 score."""

    if score >= BUY_THRESHOLD:
        return "BUY CANDIDATE"
    if score >= WATCH_THRESHOLD:
        return "WATCH"
    return "IGNORE"
