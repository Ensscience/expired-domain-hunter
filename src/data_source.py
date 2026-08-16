"""Input handling for domain candidate data.

The hunter deliberately accepts user-provided CSV files instead of depending on
one registrar or expired-domain marketplace. Unknown columns are preserved in
an ``extra`` mapping and missing optional fields default safely.
"""

from __future__ import annotations

import csv
import re
from dataclasses import dataclass, field
from pathlib import Path
from typing import Any, Iterable

DOMAIN_RE = re.compile(
    r"^(?=.{1,253}$)(?:[a-z0-9](?:[a-z0-9-]{0,61}[a-z0-9])?\.)+com$",
    re.IGNORECASE,
)

ALIASES = {
    "domain": {"domain", "domain_name", "name", "url"},
    "status": {"status", "state", "domain_status"},
    "backlinks": {"backlinks", "backlink_count", "links"},
    "ref_domains": {"ref_domains", "referring_domains", "refdomains", "rd"},
    "domain_age": {"domain_age", "age", "age_years"},
    "archive_year": {"archive_year", "first_archive_year", "first_seen_year"},
    "keyword": {"keyword", "keywords", "topic", "category"},
    "search_volume": {"search_volume", "volume", "monthly_searches"},
    "source": {"source", "provider", "list_source"},
}


@dataclass
class DomainCandidate:
    domain: str
    status: str = ""
    backlinks: float | None = None
    ref_domains: float | None = None
    domain_age: float | None = None
    archive_year: int | None = None
    keyword: str = ""
    search_volume: float | None = None
    source: str = "csv"
    extra: dict[str, Any] = field(default_factory=dict)

    @property
    def normalized_domain(self) -> str:
        return self.domain.lower().strip().rstrip(".")

    @property
    def label(self) -> str:
        return self.normalized_domain.removesuffix(".com")


def _normalise_header(value: str) -> str:
    return re.sub(r"[^a-z0-9]+", "_", value.strip().lower()).strip("_")


def _canonical_headers(headers: Iterable[str]) -> dict[str, str]:
    result: dict[str, str] = {}
    for header in headers:
        normalized = _normalise_header(header)
        for canonical, aliases in ALIASES.items():
            if normalized in aliases:
                result[canonical] = header
                break
    return result


def _number(value: Any) -> float | None:
    if value is None or str(value).strip() == "":
        return None
    cleaned = re.sub(r"[^0-9.\-]", "", str(value))
    try:
        return float(cleaned) if cleaned else None
    except ValueError:
        return None


def _integer(value: Any) -> int | None:
    number = _number(value)
    return int(number) if number is not None else None


def is_valid_com_domain(value: str) -> bool:
    """Accept only syntactically valid .COM hostnames without a URL scheme."""

    domain = value.strip().lower().rstrip(".")
    return bool(DOMAIN_RE.fullmatch(domain)) and ".." not in domain


def _get(row: dict[str, Any], headers: dict[str, str], key: str) -> Any:
    header = headers.get(key)
    return row.get(header, "") if header else ""


def parse_rows(rows: Iterable[dict[str, Any]]) -> tuple[list[DomainCandidate], list[str]]:
    """Parse CSV-like mappings, returning valid candidates and rejection notes."""

    candidates: list[DomainCandidate] = []
    rejected: list[str] = []
    rows = list(rows)
    if not rows:
        return candidates, rejected
    headers = _canonical_headers(rows[0].keys())

    for index, row in enumerate(rows, start=2):
        raw_domain = str(_get(row, headers, "domain")).strip()
        if raw_domain.startswith(("http://", "https://")):
            raw_domain = raw_domain.split("//", 1)[1].split("/", 1)[0]
        domain = raw_domain.lower().rstrip(".")
        if not is_valid_com_domain(domain):
            rejected.append(f"row {index}: invalid or non-.com domain {raw_domain!r}")
            continue

        known_headers = set(headers.values())
        extra = {key: value for key, value in row.items() if key not in known_headers}
        candidates.append(
            DomainCandidate(
                domain=domain,
                status=str(_get(row, headers, "status")).strip().lower(),
                backlinks=_number(_get(row, headers, "backlinks")),
                ref_domains=_number(_get(row, headers, "ref_domains")),
                domain_age=_number(_get(row, headers, "domain_age")),
                archive_year=_integer(_get(row, headers, "archive_year")),
                keyword=str(_get(row, headers, "keyword")).strip(),
                search_volume=_number(_get(row, headers, "search_volume")),
                source=str(_get(row, headers, "source")).strip() or "csv",
                extra=extra,
            )
        )
    return candidates, rejected


def load_domains(path: str | Path) -> tuple[list[DomainCandidate], list[str]]:
    """Load candidates from a CSV path.

    An absent or empty file is a valid state and returns an empty dataset.
    """

    csv_path = Path(path)
    if not csv_path.exists() or csv_path.stat().st_size == 0:
        return [], []
    with csv_path.open("r", encoding="utf-8-sig", newline="") as handle:
        reader = csv.DictReader(handle)
        return parse_rows(reader)
