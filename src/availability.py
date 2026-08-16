"""Point-in-time .COM hand-registration availability verification.

Verisign is the authoritative .com RDAP registry endpoint. A valid RDAP
Domain object means the name is registered or in a lifecycle state; a valid
RDAP 404 error object means the registry has no domain object at check time.
Everything inconclusive is UNKNOWN and is never alertable.
"""

from __future__ import annotations

from dataclasses import dataclass
from datetime import datetime, timezone
from typing import Any
from urllib.parse import quote

import requests

from src.data_source import is_valid_com_domain

AVAILABLE = "AVAILABLE"
REGISTERED = "REGISTERED"
PENDING = "PENDING"
AUCTION = "AUCTION"
UNKNOWN = "UNKNOWN"

RDAP_ENDPOINT = "https://rdap.verisign.com/com/v1/domain/"
USER_AGENT = "expired-domain-hunter/1.1 (+https://github.com/Ensscience/expired-domain-hunter)"


@dataclass
class AvailabilityResult:
    domain: str
    registration_status: str
    checked_at_utc: str
    rdap_url: str
    http_status: int | None = None
    reason: str = ""


class VerisignRdapChecker:
    """Conservative checker with a per-run request budget."""

    def __init__(
        self,
        session: requests.Session | None = None,
        max_requests: int = 50,
        timeout: float = 5.0,
        retries: int = 0,
    ) -> None:
        self.session = session or requests.Session()
        self.session.headers.update({"User-Agent": USER_AGENT, "Accept": "application/rdap+json, application/json"})
        self.max_requests = max(0, max_requests)
        self.timeout = max(1.0, timeout)
        self.retries = max(0, retries)
        self.requests_made = 0
        self.cache: dict[str, AvailabilityResult] = {}

    def check(self, domain: str) -> AvailabilityResult:
        normalized = domain.lower().strip().rstrip(".")
        now = datetime.now(timezone.utc).isoformat()
        url = f"{RDAP_ENDPOINT}{quote(normalized, safe='')}"
        if not is_valid_com_domain(normalized):
            return AvailabilityResult(normalized, UNKNOWN, now, url, reason="invalid .COM domain")
        if normalized in self.cache:
            return self.cache[normalized]
        if self.requests_made >= self.max_requests:
            result = AvailabilityResult(normalized, UNKNOWN, now, url, reason="availability request budget reached")
            self.cache[normalized] = result
            return result

        self.requests_made += 1
        last_result: AvailabilityResult | None = None
        for attempt in range(self.retries + 1):
            try:
                response = self.session.get(url, timeout=self.timeout)
                result = self._interpret(normalized, url, response, now)
                if result.registration_status == UNKNOWN and response.status_code in {429, 500, 502, 503, 504} and attempt < self.retries:
                    continue
                last_result = result
                break
            except (requests.RequestException, ValueError, TypeError) as exc:
                last_result = AvailabilityResult(normalized, UNKNOWN, now, url, reason=f"RDAP request failed: {type(exc).__name__}")
                if attempt >= self.retries:
                    break
        result = last_result or AvailabilityResult(normalized, UNKNOWN, now, url, reason="no RDAP response")
        self.cache[normalized] = result
        return result

    def _interpret(self, domain: str, url: str, response: Any, checked_at: str) -> AvailabilityResult:
        status_code = getattr(response, "status_code", None)
        if status_code == 404:
            try:
                payload = response.json()
            except (ValueError, TypeError, AttributeError):
                return AvailabilityResult(domain, UNKNOWN, checked_at, url, status_code, "404 response was not valid RDAP JSON")
            if isinstance(payload, dict) and int(payload.get("errorCode", 404)) == 404:
                return AvailabilityResult(domain, AVAILABLE, checked_at, url, status_code, "authoritative Verisign RDAP 404: no domain object")
            return AvailabilityResult(domain, UNKNOWN, checked_at, url, status_code, "non-standard 404 response")
        if status_code == 200:
            try:
                payload = response.json()
            except (ValueError, TypeError, AttributeError):
                return AvailabilityResult(domain, UNKNOWN, checked_at, url, status_code, "registered response was not valid RDAP JSON")
            if not isinstance(payload, dict) or payload.get("objectClassName") not in {None, "domain"}:
                return AvailabilityResult(domain, UNKNOWN, checked_at, url, status_code, "unexpected RDAP domain response")
            statuses = {str(value).lower() for value in payload.get("status", [])} if isinstance(payload.get("status", []), list) else set()
            if any(marker in status for status in statuses for marker in ("pending", "redemption", "serverhold", "clienthold")):
                return AvailabilityResult(domain, PENDING, checked_at, url, status_code, "RDAP domain object has pending or hold lifecycle status")
            return AvailabilityResult(domain, REGISTERED, checked_at, url, status_code, "authoritative Verisign RDAP domain object exists")
        if status_code in {401, 403}:
            return AvailabilityResult(domain, UNKNOWN, checked_at, url, status_code, "RDAP access was restricted")
        if status_code == 429:
            return AvailabilityResult(domain, UNKNOWN, checked_at, url, status_code, "RDAP rate limit response")
        if status_code is not None and status_code >= 500:
            return AvailabilityResult(domain, UNKNOWN, checked_at, url, status_code, "RDAP service error")
        return AvailabilityResult(domain, UNKNOWN, checked_at, url, status_code, "inconclusive RDAP response")
