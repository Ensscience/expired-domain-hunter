"""Small, respectful Wayback CDX client for shortlisted domains."""

from __future__ import annotations

import time
from dataclasses import dataclass, field
from datetime import datetime, timezone
from typing import Any
from urllib.parse import quote

import requests

from config import WAYBACK_ENDPOINT, WAYBACK_MAX_REQUESTS, WAYBACK_RETRIES, WAYBACK_TIMEOUT_SECONDS, WAYBACK_USER_AGENT

SUSPICIOUS_TERMS = {
    "casino",
    "poker",
    "betting",
    "gambling",
    "adult",
    "porn",
    "viagra",
    "pharma",
    "loan-payday",
    "crypto-airdrop",
    "malware",
    "ransomware",
    "hack",
    "escort",
    "streaming-illegal",
}
COMMERCIAL_TERMS = {
    "pricing",
    "product",
    "software",
    "saas",
    "invoice",
    "billing",
    "shop",
    "store",
    "consulting",
    "agency",
    "marketing",
    "finance",
    "accounting",
}


@dataclass
class HistorySignals:
    checked: bool = False
    snapshots: int = 0
    first_year: int | None = None
    last_year: int | None = None
    historical_quality: float = 4.0
    spam_like: bool = False
    suspicious_changes: bool = False
    previous_use: str = "No Wayback snapshot found or history was not checked."
    wayback_url: str = ""
    errors: list[str] = field(default_factory=list)


class WaybackClient:
    """Bounded client; it never queries more than the configured per-run budget."""

    def __init__(self, session: requests.Session | None = None, max_requests: int = WAYBACK_MAX_REQUESTS):
        self.session = session or requests.Session()
        self.session.headers.update({"User-Agent": WAYBACK_USER_AGENT})
        self.max_requests = max_requests
        self.requests_made = 0
        self.cache: dict[str, HistorySignals] = {}

    def inspect(self, domain: str) -> HistorySignals:
        domain = domain.lower().strip().rstrip(".")
        if domain in self.cache:
            return self.cache[domain]
        url = f"https://web.archive.org/web/*/{domain}"
        if self.requests_made >= self.max_requests:
            result = HistorySignals(wayback_url=url, errors=["Wayback request budget reached; history not checked."])
            self.cache[domain] = result
            return result

        self.requests_made += 1
        params = {
            "url": f"{domain}/*",
            "output": "json",
            "fl": "timestamp,original,statuscode,mimetype,digest",
            "filter": "statuscode:200",
            "collapse": "digest",
            "limit": "8",
            "from": "1996",
            "to": str(datetime.now(timezone.utc).year),
        }
        rows: list[dict[str, Any]] = []
        errors: list[str] = []
        for attempt in range(WAYBACK_RETRIES + 1):
            try:
                response = self.session.get(WAYBACK_ENDPOINT, params=params, timeout=WAYBACK_TIMEOUT_SECONDS)
                if response.status_code == 429:
                    retry_after = min(float(response.headers.get("Retry-After", "2")), 10.0)
                    if attempt < WAYBACK_RETRIES:
                        time.sleep(retry_after)
                        continue
                response.raise_for_status()
                payload = response.json()
                rows = self._rows_from_payload(payload)
                break
            except (requests.RequestException, ValueError) as exc:
                errors.append(f"{type(exc).__name__}: {exc}")
                if attempt < WAYBACK_RETRIES:
                    time.sleep(1.0 + attempt)

        result = self._signals_from_rows(domain, rows, url, errors)
        self.cache[domain] = result
        return result

    @staticmethod
    def _rows_from_payload(payload: Any) -> list[dict[str, Any]]:
        if not isinstance(payload, list) or not payload:
            return []
        if isinstance(payload[0], list):
            headers = [str(value) for value in payload[0]]
            return [dict(zip(headers, row)) for row in payload[1:] if isinstance(row, list)]
        if isinstance(payload[0], dict):
            return payload
        return []

    @staticmethod
    def _signals_from_rows(domain: str, rows: list[dict[str, Any]], url: str, errors: list[str]) -> HistorySignals:
        timestamps = [str(row.get("timestamp", "")) for row in rows if str(row.get("timestamp", ""))[:4].isdigit()]
        years = [int(timestamp[:4]) for timestamp in timestamps]
        originals = [str(row.get("original", "")).lower() for row in rows]
        suspicious_hits = sorted({term for term in SUSPICIOUS_TERMS if any(term in original for original in originals)})
        commercial_hits = sorted({term for term in COMMERCIAL_TERMS if any(term in original for original in originals)})
        spam_like = bool(suspicious_hits)
        # A theme shift is only flagged when both a clean commercial signal and
        # a high-risk signal are visible in the limited sample; it is not a
        # substitute for legal, SEO, or manual history review.
        suspicious_changes = bool(suspicious_hits and commercial_hits)
        if not rows:
            quality = 4.0
            usage = "No usable HTTP 200 snapshot returned."
        elif spam_like:
            quality = 1.5
            usage = f"Suspicious historical URL signals: {', '.join(suspicious_hits)}."
        elif commercial_hits:
            quality = 9.0
            usage = f"Historical URL signals include commercial themes: {', '.join(commercial_hits)}."
        else:
            quality = 7.0
            usage = "Historical snapshots exist; sampled URLs did not show obvious spam terms."
        if errors and not rows:
            usage = "Wayback history could not be checked reliably this run."
        return HistorySignals(
            checked=True,
            snapshots=len(rows),
            first_year=min(years) if years else None,
            last_year=max(years) if years else None,
            historical_quality=quality,
            spam_like=spam_like,
            suspicious_changes=suspicious_changes,
            previous_use=usage,
            wayback_url=url,
            errors=errors,
        )


def wayback_url(domain: str) -> str:
    """Return a stable human-review URL without making a network request."""

    return f"https://web.archive.org/web/*/{quote(domain.lower().strip().rstrip('.'))}"
