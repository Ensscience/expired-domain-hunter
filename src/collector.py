"""Automatic collection from public expired-domain feeds.

The collector uses only public raw files. It does not scrape interactive
marketplaces, follow auction listings, or bypass authentication and anti-bot
controls. Source lifecycle labels are preserved so downstream reporting can
exclude non-expired inventory.
"""

from __future__ import annotations

import csv
import hashlib
import io
import json
import time
from dataclasses import asdict, dataclass, field
from datetime import datetime, timezone
from email.utils import parsedate_to_datetime
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
    etag: str = ""
    last_modified: str = ""
    dataset_date: str = ""
    content_sha256: str = ""
    error: str = ""


@dataclass
class CollectionResult:
    generated_at_utc: str
    source: str
    output_path: str
    fallback_used: bool
    dataset_id: str
    dataset_date: str
    dataset_date_source: str
    source_report_path: str
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


def _header_date(value: str) -> str:
    if not value:
        return ""
    try:
        return parsedate_to_datetime(value).astimezone(timezone.utc).date().isoformat()
    except (TypeError, ValueError, OverflowError):
        return ""


def _response_metadata(response: Any) -> dict[str, str]:
    headers = getattr(response, "headers", {}) or {}
    text = str(getattr(response, "text", ""))
    content = getattr(response, "content", None)
    payload = content if isinstance(content, (bytes, bytearray)) else text.encode("utf-8")
    last_modified = str(headers.get("Last-Modified", headers.get("last-modified", "")))
    return {
        "etag": str(headers.get("ETag", headers.get("etag", ""))),
        "last_modified": last_modified,
        "dataset_date": _header_date(last_modified),
        "content_sha256": hashlib.sha256(payload).hexdigest(),
    }


def _download_text(
    feed: str,
    url: str,
    session: requests.Session,
    timeout: float,
    retries: int,
) -> tuple[str, str, dict[str, str]]:
    last_error = ""
    for attempt in range(retries + 1):
        try:
            response = session.get(url, timeout=timeout)
            response.raise_for_status()
            return str(response.text), "", _response_metadata(response)
        except (requests.RequestException, UnicodeError) as exc:
            last_error = f"{type(exc).__name__}: {exc}"
            if attempt < retries:
                time.sleep(1.0 + attempt)
    return "", last_error or f"Unable to download {feed} feed.", {}


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


def _dataset_id(feed_results: list[FeedResult], fallback_hash: str = "") -> str:
    if fallback_hash:
        return f"fallback:{fallback_hash}"
    identities = [
        {
            "feed": item.feed,
            "url": item.url,
            "status": item.status,
            "etag": item.etag,
            "last_modified": item.last_modified,
            "content_sha256": item.content_sha256,
        }
        for item in feed_results
    ]
    payload = json.dumps(identities, sort_keys=True, separators=(",", ":")).encode("utf-8")
    return f"feeds:{hashlib.sha256(payload).hexdigest()}"


def _dataset_date(feed_results: list[FeedResult], generated_at_utc: str) -> tuple[str, str]:
    dates = sorted({item.dataset_date for item in feed_results if item.dataset_date})
    if dates:
        return dates[-1], "feed Last-Modified metadata"
    return generated_at_utc[:10], "UTC collection date; source publication date unavailable"


def _write_source_report(path: Path, result: CollectionResult) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    fields = [
        "dataset_id", "dataset_date", "dataset_date_source", "feed", "source", "lifecycle", "url", "status",
        "downloaded_lines", "valid_com", "invalid_lines", "etag", "last_modified", "content_sha256", "error",
    ]
    with path.open("w", encoding="utf-8", newline="") as handle:
        writer = csv.DictWriter(handle, fieldnames=fields)
        writer.writeheader()
        for feed in result.feeds:
            writer.writerow({
                "dataset_id": result.dataset_id,
                "dataset_date": result.dataset_date,
                "dataset_date_source": result.dataset_date_source,
                "feed": feed.feed,
                "source": _source_label(feed.feed),
                "lifecycle": feed.lifecycle,
                "url": feed.url,
                "status": feed.status,
                "downloaded_lines": feed.downloaded_lines,
                "valid_com": feed.valid_com,
                "invalid_lines": feed.invalid_lines,
                "etag": feed.etag,
                "last_modified": feed.last_modified,
                "content_sha256": feed.content_sha256,
                "error": feed.error,
            })


def collect_domains(
    output_path: str | Path,
    summary_path: str | Path,
    session: requests.Session | None = None,
    feeds: dict[str, str] | None = None,
    timeout: float = 30.0,
    retries: int = 2,
    fallback_path: str | Path | None = None,
    source_report_path: str | Path | None = None,
) -> CollectionResult:
    """Download public expired/dropped feeds and write normalized `.COM` rows."""

    output = Path(output_path)
    summary = Path(summary_path)
    source_report = Path(source_report_path) if source_report_path else summary.parent / "source_report.csv"
    source_feeds = feeds or DEFAULT_FEEDS
    client = session or requests.Session()
    client.headers.update({"User-Agent": COLLECTOR_USER_AGENT})
    feed_results: list[FeedResult] = []
    records_by_domain: dict[str, dict[str, str]] = {}
    collected_lines = 0
    rejected_lines = 0
    source_breakdown: dict[str, int] = {}

    for feed, url in source_feeds.items():
        text, error, metadata = _download_text(feed, url, client, timeout, retries)
        if error:
            feed_results.append(FeedResult("error", feed, url, _lifecycle(feed), error=error))
            continue
        records, feed_result = _parse_feed(feed, text, url)
        for key, value in metadata.items():
            setattr(feed_result, key, value)
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

    generated_at_utc = datetime.now(timezone.utc).isoformat()
    fallback_used = False
    fallback_hash = ""
    if not records_by_domain:
        if fallback_path and Path(fallback_path).exists() and Path(fallback_path).stat().st_size > 0:
            fallback_used = True
            fallback_hash = hashlib.sha256(Path(fallback_path).read_bytes()).hexdigest()
        else:
            errors = "; ".join(f"{item.feed}: {item.error}" for item in feed_results if item.error)
            raise CollectionError(f"No valid .COM domains collected from public feeds. {errors}".strip())
    else:
        _write_input(output, [records_by_domain[key] for key in sorted(records_by_domain)])

    dataset_id = _dataset_id(feed_results, fallback_hash)
    dataset_date, dataset_date_source = _dataset_date(feed_results, generated_at_utc)
    result = CollectionResult(
        generated_at_utc=generated_at_utc,
        source="WhoisFreaks public expired/dropped feeds + UniqueDomains public expired extract" if not fallback_used else "manual CSV fallback",
        output_path=str(output),
        fallback_used=fallback_used,
        dataset_id=dataset_id,
        dataset_date=dataset_date,
        dataset_date_source=dataset_date_source,
        source_report_path=str(source_report),
        collected_lines=collected_lines,
        unique_com_domains=len(records_by_domain),
        expired_com_domains=sum(item.valid_com for item in feed_results if item.lifecycle == "expired"),
        dropped_com_domains=sum(item.valid_com for item in feed_results if item.lifecycle == "dropped"),
        duplicate_domains=max(0, sum(item.valid_com for item in feed_results) - len(records_by_domain)),
        rejected_lines=rejected_lines,
        source_breakdown=source_breakdown,
        feeds=feed_results,
    )
    _write_summary(summary, result)
    _write_source_report(source_report, result)
    return result
