"""Automatic collection from public expired-domain feeds.

The collector uses only public raw files. It does not scrape interactive
marketplaces, follow auction listings, or bypass authentication and anti-bot
controls. Source lifecycle labels are preserved so downstream availability
verification can reject non-hand-registration inventory.
"""

from __future__ import annotations

import csv
import io
import json
import time
from dataclasses import asdict, dataclass, field
from datetime import datetime, timezone
from pathlib import Path
from typing import Any, Iterable

import requests

from src.data_source import is_valid_com_domain

DEFAULT_FEEDS = {
    "whoisfreaks_expired": "https://raw.githubusercontent.com/WhoisFreaks/daily-expired-and-dropped-domains/main/0-latest-free-expired-domains.csv",
    "whoisfreaks_dropped": "https://raw.githubusercontent.com/WhoisFreaks/daily-expired-and-dropped-domains/main/0-latest-free-dropped-domains.csv",
    "uniquedomains_expired": "https://raw.githubusercontent.com/UniqueDomains/expired-oneword-domains/main/expired.csv",
}
COLLECTOR_USER_AGENT = "expired-domain-hunter/1.1 (+https://github.com/Ensscience/expired-domain-hunter)"
ALLOWED_LIFECYCLE = {"expired", "dropped", "deleted"}
EXCLUDED_LIFECYCLE_MARKERS = {
    "pending",
    "auction",
    "backorder",
    "expiring",
    "pre-release",
    "prerelease",
    "buy now",
    "aftermarket",
    "bidding",
}


@dataclass
class CollectedDomain:
    domain: str
    status: str
    source: str
    source_url: str


@dataclass
class FeedResult:
    status: str
    feed: str
    url: str
    lifecycle: str
    downloaded_lines: int = 0
    valid_com: int = 0
    invalid_lines: int = 0
    error: str = ""


@dataclass
class CollectionResult:
    generated_at_utc: str
    source: str
    output_path: str
    fallback_used: bool
    collected_lines: int
    unique_com_domains: int
    expired_com_domains: int
    dropped_com_domains: int
    duplicate_domains: int
    rejected_lines: int
    source_breakdown: dict[str, int] = field(default_factory=dict)
    feeds: list[FeedResult] = field(default_factory=list)


class CollectionError(RuntimeError):
    """Raised when public feeds fail and no manual fallback is available."""


def _clean_domain(line: str) -> str:
    value = line.strip().strip('"').strip().lower().rstrip(".")
    if "," in value:
        value = value.split(",", 1)[0].strip()
    if value.startswith(("http://", "https://")):
        value = value.split("//", 1)[1].split("/", 1)[0]
    return value


def _source_label(feed: str) -> str:
    if feed.startswith("uniquedomains"):
        return "uniquedomains-public-extract"
    return "whoisfreaks-public-github"


def _lifecycle(feed: str) -> str:
    return "dropped" if "dropped" in feed else "expired"


def _download_text(
    feed: str,
    url: str,
    session: requests.Session,
    timeout: float,
    retries: int,
) -> tuple[str, str]:
    last_error = ""
    for attempt in range(retries + 1):
        try:
            response = session.get(url, timeout=timeout)
            response.raise_for_status()
            return response.text, ""
        except (requests.RequestException, UnicodeError) as exc:
            last_error = f"{type(exc).__name__}: {exc}"
            if attempt < retries:
                time.sleep(1.0 + attempt)
    return "", last_error or f"Unable to download {feed} feed."


def _is_allowed_status(value: str, default_lifecycle: str) -> bool:
    status = value.strip().lower() or default_lifecycle
    if any(marker in status for marker in EXCLUDED_LIFECYCLE_MARKERS):
        return False
    return any(word in status for word in ALLOWED_LIFECYCLE)


def _parse_feed(feed: str, text: str, url: str) -> tuple[list[CollectedDomain], FeedResult]:
    lifecycle = _lifecycle(feed)
    source = _source_label(feed)
    records: list[CollectedDomain] = []
    invalid = 0

    # UniqueDomains publishes a real CSV extract; WhoisFreaks publishes one
    # domain per line despite the .csv filename.
    if "uniquedomains" in feed or text.lstrip().lower().startswith("id,domain,status"):
        rows = csv.DictReader(io.StringIO(text))
        raw_count = 0
        for row in rows:
            raw_count += 1
            raw_status = str(row.get("status", lifecycle)).strip().lower()
            raw_domain = str(row.get("domain", ""))
            domain = _clean_domain(raw_domain)
            if not _is_allowed_status(raw_status, lifecycle) or not is_valid_com_domain(domain):
                invalid += 1
                continue
            records.append(CollectedDomain(domain=domain, status="expired" if "expired" in raw_status else lifecycle, source=source, source_url=url))
        feed_result = FeedResult("ok", feed, url, lifecycle, raw_count, len(records), invalid)
        return records, feed_result

    lines = text.splitlines()
    for line in lines:
        domain = _clean_domain(line)
        if not domain or domain.startswith("#") or domain in {"domain", "domains"}:
            continue
        if not is_valid_com_domain(domain):
            invalid += 1
            continue
        records.append(CollectedDomain(domain=domain, status=lifecycle, source=source, source_url=url))
    feed_result = FeedResult("ok", feed, url, lifecycle, len(lines), len(records), invalid)
    return records, feed_result


def _merge_record(existing: dict[str, str], record: CollectedDomain) -> None:
    statuses = set(existing["status"].split(";")) | {record.status}
    sources = set(existing["sources"].split(";")) | {record.source}
    existing["status"] = ";".join(sorted(statuses))
    existing["source_count"] = str(len(sources))
    existing["sources"] = ";".join(sorted(sources))
    # Keep a compact primary source column while retaining the full list.
    existing["source"] = ";".join(sorted(sources))


def _write_input(path: Path, records: Iterable[dict[str, str]]) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    with path.open("w", encoding="utf-8", newline="") as handle:
        writer = csv.DictWriter(handle, fieldnames=["domain", "status", "source", "source_count", "sources"])
        writer.writeheader()
        writer.writerows(records)


def _write_summary(path: Path, result: CollectionResult) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(json.dumps(asdict(result), indent=2) + "\n", encoding="utf-8")


def collect_domains(
    output_path: str | Path,
    summary_path: str | Path,
    session: requests.Session | None = None,
    feeds: dict[str, str] | None = None,
    timeout: float = 30.0,
    retries: int = 2,
    fallback_path: str | Path | None = None,
) -> CollectionResult:
    """Download public expired/dropped feeds and write normalized `.COM` rows."""

    output = Path(output_path)
    summary = Path(summary_path)
    source_feeds = feeds or DEFAULT_FEEDS
    client = session or requests.Session()
    client.headers.update({"User-Agent": COLLECTOR_USER_AGENT})
    feed_results: list[FeedResult] = []
    records_by_domain: dict[str, dict[str, str]] = {}
    collected_lines = 0
    rejected_lines = 0
    source_breakdown: dict[str, int] = {}

    for feed, url in source_feeds.items():
        text, error = _download_text(feed, url, client, timeout, retries)
        if error:
            feed_results.append(FeedResult("error", feed, url, _lifecycle(feed), error=error))
            continue
        records, feed_result = _parse_feed(feed, text, url)
        feed_results.append(feed_result)
        collected_lines += feed_result.downloaded_lines
        rejected_lines += feed_result.invalid_lines
        source_breakdown[feed] = len(records)
        for record in records:
            if record.domain in records_by_domain:
                _merge_record(records_by_domain[record.domain], record)
            else:
                records_by_domain[record.domain] = {
                    "domain": record.domain,
                    "status": record.status,
                    "source": record.source,
                    "source_count": "1",
                    "sources": record.source,
                }

    fallback_used = False
    if not records_by_domain:
        if fallback_path and Path(fallback_path).exists() and Path(fallback_path).stat().st_size > 0:
            fallback_used = True
        else:
            errors = "; ".join(f"{item.feed}: {item.error}" for item in feed_results if item.error)
            raise CollectionError(f"No valid .COM domains collected from public feeds. {errors}".strip())
    else:
        _write_input(output, [records_by_domain[key] for key in sorted(records_by_domain)])

    expired_count = sum(item.valid_com for item in feed_results if item.lifecycle == "expired")
    dropped_count = sum(item.valid_com for item in feed_results if item.lifecycle == "dropped")
    result = CollectionResult(
        generated_at_utc=datetime.now(timezone.utc).isoformat(),
        source="WhoisFreaks public expired/dropped feeds + UniqueDomains public expired extract",
        output_path=str(output),
        fallback_used=fallback_used,
        collected_lines=collected_lines,
        unique_com_domains=len(records_by_domain),
        expired_com_domains=expired_count,
        dropped_com_domains=dropped_count,
        duplicate_domains=max(0, expired_count + dropped_count - len(records_by_domain)),
        rejected_lines=rejected_lines,
        source_breakdown=source_breakdown,
        feeds=feed_results,
    )
    _write_summary(summary, result)
    return result
