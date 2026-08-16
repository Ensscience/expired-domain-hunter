"""Automatic collection from the public WhoisFreaks GitHub feed.

The feed is intentionally consumed as public raw files, not scraped from an
interactive site. Each run makes exactly one request per feed, uses a modest
timeout/retry policy, filters to .COM locally, and writes a normalized CSV for
the existing hunter.
"""

from __future__ import annotations

import csv
import json
import time
from dataclasses import asdict, dataclass, field
from datetime import datetime, timezone
from pathlib import Path
from typing import Any, Iterable

import requests

from src.data_source import is_valid_com_domain

DEFAULT_FEEDS = {
    "expired": "https://raw.githubusercontent.com/WhoisFreaks/daily-expired-and-dropped-domains/main/0-latest-free-expired-domains.csv",
    "dropped": "https://raw.githubusercontent.com/WhoisFreaks/daily-expired-and-dropped-domains/main/0-latest-free-dropped-domains.csv",
}
COLLECTOR_USER_AGENT = "expired-domain-hunter/1.0 (+https://github.com/Ensscience/expired-domain-hunter)"


@dataclass
class FeedResult:
    status: str
    feed: str
    url: str
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
    feeds: list[FeedResult] = field(default_factory=list)


class CollectionError(RuntimeError):
    """Raised when the public feed cannot be collected and no fallback applies."""


def _clean_domain(line: str) -> str:
    value = line.strip().strip('"').strip().lower().rstrip(".")
    if "," in value:
        value = value.split(",", 1)[0].strip()
    if value.startswith(("http://", "https://")):
        value = value.split("//", 1)[1].split("/", 1)[0]
    return value


def _download_lines(
    feed: str,
    url: str,
    session: requests.Session,
    timeout: float,
    retries: int,
) -> tuple[list[str], str]:
    last_error = ""
    for attempt in range(retries + 1):
        try:
            response = session.get(url, timeout=timeout)
            response.raise_for_status()
            return response.text.splitlines(), ""
        except (requests.RequestException, UnicodeError) as exc:
            last_error = f"{type(exc).__name__}: {exc}"
            if attempt < retries:
                time.sleep(1.0 + attempt)
    return [], last_error or f"Unable to download {feed} feed."


def _feed_domains(feed: str, lines: Iterable[str], url: str) -> tuple[list[str], FeedResult]:
    line_list = list(lines)
    domains: list[str] = []
    invalid = 0
    for line in line_list:
        domain = _clean_domain(line)
        if not domain or domain in {"domain", "domains"} or domain.startswith("#"):
            continue
        if is_valid_com_domain(domain):
            domains.append(domain)
        else:
            invalid += 1
    result = FeedResult(
        status="ok",
        feed=feed,
        url=url,
        downloaded_lines=len(line_list),
        valid_com=len(domains),
        invalid_lines=invalid,
    )
    return domains, result


def _write_input(path: Path, records: list[dict[str, str]]) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    with path.open("w", encoding="utf-8", newline="") as handle:
        writer = csv.DictWriter(handle, fieldnames=["domain", "status", "source"])
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
    """Download both public feeds and write normalized `.COM` candidates.

    If all feeds fail and ``fallback_path`` exists, the fallback file is left
    untouched and the summary records that fact. No synthetic domain data is
    created.
    """

    output = Path(output_path)
    summary = Path(summary_path)
    source_feeds = feeds or DEFAULT_FEEDS
    client = session or requests.Session()
    client.headers.update({"User-Agent": COLLECTOR_USER_AGENT})
    feed_results: list[FeedResult] = []
    records_by_domain: dict[str, dict[str, str]] = {}
    collected_lines = 0
    rejected_lines = 0
    expired_count = 0
    dropped_count = 0

    for feed, url in source_feeds.items():
        lines, error = _download_lines(feed, url, client, timeout, retries)
        if error:
            feed_results.append(FeedResult(status="error", feed=feed, url=url, error=error))
            continue
        domains, feed_result = _feed_domains(feed, lines, url)
        feed_results.append(feed_result)
        collected_lines += feed_result.downloaded_lines
        rejected_lines += feed_result.invalid_lines
        if feed == "expired":
            expired_count += len(domains)
        elif feed == "dropped":
            dropped_count += len(domains)
        for domain in domains:
            if domain in records_by_domain:
                existing = records_by_domain[domain]
                existing["status"] = ";".join(sorted(set(existing["status"].split(";") + [feed])))
                existing["source"] = "whoisfreaks-free-github:expired+dropped"
            else:
                records_by_domain[domain] = {
                    "domain": domain,
                    "status": feed,
                    "source": f"whoisfreaks-free-github:{feed}",
                }

    fallback_used = False
    if not records_by_domain:
        if fallback_path and Path(fallback_path).exists() and Path(fallback_path).stat().st_size > 0:
            fallback_used = True
        else:
            errors = "; ".join(f"{item.feed}: {item.error}" for item in feed_results if item.error)
            raise CollectionError(f"No valid .COM domains collected from the public feeds. {errors}".strip())
    else:
        _write_input(output, [records_by_domain[key] for key in sorted(records_by_domain)])

    result = CollectionResult(
        generated_at_utc=datetime.now(timezone.utc).isoformat(),
        source="WhoisFreaks public GitHub daily expired/dropped feed",
        output_path=str(output),
        fallback_used=fallback_used,
        collected_lines=collected_lines,
        unique_com_domains=len(records_by_domain),
        expired_com_domains=expired_count,
        dropped_com_domains=dropped_count,
        duplicate_domains=max(0, expired_count + dropped_count - len(records_by_domain)),
        rejected_lines=rejected_lines,
        feeds=feed_results,
    )
    _write_summary(summary, result)
    return result
