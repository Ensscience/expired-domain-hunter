#!/usr/bin/env python3
"""Collect, score, verify, and report only high-quality available expired .COM domains."""

from __future__ import annotations

import argparse
import csv
import json
import sys
from datetime import datetime, timezone
from pathlib import Path

from config import (
    AVAILABILITY_MAX_REQUESTS,
    AVAILABILITY_RETRIES,
    AVAILABILITY_TIMEOUT_SECONDS,
    DEFAULT_INPUT_PATH,
    DEFAULT_OUTPUT_DIR,
    DEFAULT_STATE_PATH,
    DEFAULT_TOP_N,
    FINAL_SCORE_THRESHOLD,
    WAYBACK_MAX_REQUESTS,
)
from src.availability import AUCTION, AVAILABLE, PENDING, REGISTERED, UNKNOWN, AvailabilityResult, VerisignRdapChecker, rdap_url
from src.data_source import DomainCandidate, load_domains
from src.filters import FilterResult, inspect_candidate
from src.history import HistorySignals, WaybackClient, wayback_url
from src.scoring import Evaluation, evaluate
from src.state import ProcessState
from src.telegram import send_daily_summary, send_test_message

RESULT_COLUMNS = [
    "rank", "domain", "score", "score_scale", "classification", "availability_status", "source", "source_count", "sources",
    "natural_language_quality", "brandability", "commercial_intent", "shortness_memorability", "keyword_quality",
    "resale_potential", "broad_clean_market_appeal", "historical_quality", "backlink_quality", "age_history", "end_user_potential",
    "penalty_total", "spam_risk", "potential_industries", "reason", "main_strength", "main_weakness", "trademark_risk_flag", "wayback_url",
]


ALLOWED_SOURCE_LIFECYCLE = "expired"


def _source_count(candidate: DomainCandidate) -> int:
    try:
        return max(1, int(candidate.extra.get("source_count", 1)))
    except (TypeError, ValueError):
        return 1


def _sources(candidate: DomainCandidate) -> str:
    return str(candidate.extra.get("sources", candidate.source or "unknown"))


def _lifecycle_status(candidate: DomainCandidate) -> str:
    status = candidate.status.strip().lower()
    if status == ALLOWED_SOURCE_LIFECYCLE:
        return ALLOWED_SOURCE_LIFECYCLE.upper()
    if any(marker in status for marker in ("pending", "redemption", "hold", "reserved")):
        return PENDING
    if any(marker in status for marker in ("auction", "backorder", "bidding", "aftermarket", "marketplace", "buy now", "expiring", "pre-release", "prerelease")):
        return AUCTION
    return UNKNOWN


def _neutral_history(domain: str) -> HistorySignals:
    return HistorySignals(
        checked=False,
        historical_quality=4.0,
        previous_use="History deferred until after AVAILABLE verification.",
        wayback_url=wayback_url(domain),
    )


def _initial_rank_key(item: tuple[DomainCandidate, FilterResult, Evaluation]) -> tuple[float, int, float, float, float, str]:
    candidate, _, evaluation = item
    return (
        -evaluation.score,
        len(candidate.label),
        -evaluation.commercial_intent,
        -evaluation.keyword_quality,
        -evaluation.brandability - evaluation.end_user_potential,
        candidate.normalized_domain,
    )


def _final_rank_key(item: Evaluation) -> tuple[float, float, float, float, float, str]:
    return (
        -item.score,
        -item.natural_language_quality,
        -item.commercial_intent,
        -item.brandability,
        -item.shortness_memorability,
        item.domain,
    )


def _evaluation_row(item: Evaluation, rank: int) -> dict[str, object]:
    row = {key: getattr(item, key, "") for key in RESULT_COLUMNS if key not in {"rank", "availability_status"}}
    row["rank"] = rank
    row["availability_status"] = item.registration_status
    return row


def write_results(evaluations: list[Evaluation], output_dir: Path, summary: dict[str, object]) -> tuple[Path, Path, Path]:
    output_dir.mkdir(parents=True, exist_ok=True)
    results_path = output_dir / "results.csv"
    top_path = output_dir / "top_domains.txt"
    summary_path = output_dir / "run_summary.json"

    with results_path.open("w", encoding="utf-8", newline="") as handle:
        writer = csv.DictWriter(handle, fieldnames=RESULT_COLUMNS)
        writer.writeheader()
        writer.writerows(_evaluation_row(item, rank) for rank, item in enumerate(evaluations, start=1))

    with top_path.open("w", encoding="utf-8") as handle:
        handle.write("🔥 TOP AVAILABLE EXPIRED .COM\n")
        handle.write("Score threshold: AVAILABLE and score >= 7.0/10\n")
        handle.write("RDAP availability is point-in-time; verify at a registrar immediately before registration.\n\n")
        if not evaluations:
            handle.write("🔎 No high-quality available expired .COM domains were verified in this dataset.\n")
        else:
            for rank, item in enumerate(evaluations, start=1):
                handle.write(f"{rank}. {item.domain}\n")
                handle.write(f"   Score: {item.score:.1f}/10 | Classification: {item.classification} | Availability: {item.registration_status}\n")
                handle.write(f"   Source: {item.sources}\n")
                handle.write(f"   Why it is valuable: {item.reason}\n")
                handle.write(f"   Main strength: {item.main_strength}\n")
                handle.write(f"   Main weakness: {item.main_weakness}\n")
                handle.write(f"   Estimated resale: {item.estimated_resale_range}\n")
                if item.trademark_risk_flag:
                    handle.write(f"   Trademark risk: {item.trademark_risk_flag}\n")
                if item.wayback_url:
                    handle.write(f"   RDAP/Wayback: {item.wayback_url}\n")
                handle.write("\n")

    summary_path.write_text(json.dumps(summary, indent=2) + "\n", encoding="utf-8")
    return results_path, top_path, summary_path


def _unique_candidates(candidates: list[DomainCandidate]) -> tuple[list[DomainCandidate], int]:
    seen: set[str] = set()
    unique: list[DomainCandidate] = []
    duplicates = 0
    for candidate in candidates:
        if candidate.normalized_domain in seen:
            duplicates += 1
            continue
        seen.add(candidate.normalized_domain)
        unique.append(candidate)
    return unique, duplicates


def _make_initial_evaluation(candidate: DomainCandidate, filter_result: FilterResult) -> Evaluation:
    item = evaluate(candidate, filter_result, _neutral_history(candidate.normalized_domain))
    item.status = candidate.status
    item.registration_status = UNKNOWN
    item.source_count = _source_count(candidate)
    item.sources = _sources(candidate)
    item.score_stage = "INITIAL"
    item.wayback_url = rdap_url(candidate.normalized_domain)
    item.reason = f"{item.reason}; Availability is unverified until authoritative Verisign RDAP."
    return item


def run(
    input_path: Path,
    output_dir: Path,
    skip_wayback: bool = False,
    max_wayback: int = WAYBACK_MAX_REQUESTS,
    *,
    availability_checker: VerisignRdapChecker | None = None,
    state: ProcessState | None = None,
    max_availability: int = AVAILABILITY_MAX_REQUESTS,
    availability_timeout: float = AVAILABILITY_TIMEOUT_SECONDS,
    availability_retries: int = AVAILABILITY_RETRIES,
    dataset_id: str = "",
    dataset_date: str = "",
    dataset_source: str = "",
) -> tuple[list[Evaluation], dict[str, int], list[str]]:
    loaded_candidates, rejected_rows = load_domains(input_path)
    candidates, duplicate_count = _unique_candidates(loaded_candidates)
    history_client = WaybackClient(max_requests=max_wayback)
    checker = availability_checker or VerisignRdapChecker(max_requests=max_availability, timeout=availability_timeout, retries=availability_retries)
    stats: dict[str, int] = {
        "input_rows": len(candidates), "raw_loaded_rows": len(loaded_candidates), "duplicates": duplicate_count,
        "rejected": len(rejected_rows), "lifecycle_filtered": 0, "availability_checked": 0, "available": 0,
        "registered": 0, "pending": 0, "auction": 0, "unknown": 0, "quality_filtered": 0,
        "initial_scored": 0, "rdap_selected": 0, "rdap_deferred": 0, "final_threshold_rejected": 0,
        "state_skipped": 0, "evaluated": 0, "wayback_requests": 0,
    }

    initial_ranked: list[tuple[DomainCandidate, FilterResult, Evaluation]] = []
    for candidate in candidates:
        lifecycle = _lifecycle_status(candidate)
        if lifecycle != ALLOWED_SOURCE_LIFECYCLE.upper():
            stats["lifecycle_filtered"] += 1
            if lifecycle.lower() in {"pending", "auction", "unknown"}:
                stats[lifecycle.lower()] += 1
            continue
        filter_result = inspect_candidate(candidate)
        if not filter_result.accepted or filter_result.spam_signal:
            stats["quality_filtered"] += 1
            continue
        initial_ranked.append((candidate, filter_result, _make_initial_evaluation(candidate, filter_result)))
        stats["initial_scored"] += 1

    initial_ranked.sort(key=_initial_rank_key)
    rdap_candidates = initial_ranked[: max(0, max_availability)]
    stats["rdap_selected"] = len(rdap_candidates)
    stats["rdap_deferred"] = max(0, len(initial_ranked) - len(rdap_candidates))
    final_evaluations: list[Evaluation] = []

    for candidate, filter_result, initial in rdap_candidates:
        registration: AvailabilityResult = checker.check(candidate.normalized_domain)
        stats["availability_checked"] += 1
        status_key = registration.registration_status.lower()
        if status_key in {"available", "registered", "pending", "auction", "unknown"}:
            stats[status_key] += 1
        if state:
            state.record(candidate.normalized_domain, registration.registration_status, registration.checked_at_utc, score=initial.score, reason=registration.reason)

        if registration.registration_status != AVAILABLE:
            continue

        if skip_wayback:
            history = HistorySignals(
                checked=False,
                historical_quality=4.0,
                wayback_url=wayback_url(candidate.normalized_domain),
                previous_use="Wayback check skipped for this run.",
            )
        else:
            history = history_client.inspect(candidate.normalized_domain)
        item = evaluate(candidate, filter_result, history)
        item.score_stage = "FINAL"
        item.status = candidate.status
        item.registration_status = AVAILABLE
        item.source_count = _source_count(candidate)
        item.sources = _sources(candidate)
        item.wayback_url = registration.rdap_url or rdap_url(candidate.normalized_domain)
        item.reason = f"{item.reason}; Availability verified by authoritative Verisign RDAP 404."
        stats["evaluated"] += 1
        if item.score >= FINAL_SCORE_THRESHOLD:
            final_evaluations.append(item)
        else:
            stats["final_threshold_rejected"] += 1

    final_evaluations.sort(key=_final_rank_key)
    final_evaluations = final_evaluations[:DEFAULT_TOP_N]
    stats["wayback_requests"] = history_client.requests_made
    summary = {
        "generated_at_utc": datetime.now(timezone.utc).isoformat(),
        "dataset_id": dataset_id,
        "dataset_date": dataset_date,
        "dataset_source": dataset_source,
        "raw_loaded_rows": stats["raw_loaded_rows"],
        "total_input_candidates": stats["input_rows"],
        "duplicate_domains": stats["duplicates"],
        "rejected_rows": stats["rejected"],
        "lifecycle_filtered": stats["lifecycle_filtered"],
        "quality_candidates": stats["initial_scored"],
        "initial_scored_candidates": stats["initial_scored"],
        "rdap_checked": stats["availability_checked"],
        "rdap_selected_candidates": stats["rdap_selected"],
        "rdap_deferred_candidates": stats["rdap_deferred"],
        "wayback_requests_made": stats["wayback_requests"],
        "availability_requests_made": checker.requests_made,
        "status_counts": {key: stats[key.lower()] for key in (AVAILABLE, REGISTERED, PENDING, AUCTION, UNKNOWN)},
        "final_threshold_rejected": stats["final_threshold_rejected"],
        "top50_count": len(final_evaluations),
        "final_candidates": len(final_evaluations),
        "buy_candidates": len(final_evaluations),
        "highest_score": max((item.score for item in final_evaluations), default=0.0),
        "selection_strategy": "Initial quality ranking selects the strongest RDAP candidates; only AVAILABLE domains scoring >= 7.0/10 receive Wayback/final ranking; final output is capped at 50.",
        "scoring_note": "Strict 0–10 investor score. Final Telegram candidates require AVAILABLE and score >= 7.0/10; UNKNOWN, REGISTERED, PENDING, and AUCTION are rejected.",
    }
    write_results(final_evaluations, output_dir, summary)
    return final_evaluations, stats, rejected_rows


def _read_collection_metadata(path: Path) -> dict[str, str]:
    if not path.exists() or not path.stat().st_size:
        return {}
    try:
        value = json.loads(path.read_text(encoding="utf-8"))
    except (OSError, ValueError, TypeError):
        return {}
    return value if isinstance(value, dict) else {}


def build_parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(description="Find and report only currently AVAILABLE expired .COM domains scoring at least 7.0/10.")
    parser.add_argument("--input", type=Path, default=DEFAULT_INPUT_PATH, help="CSV input path")
    parser.add_argument("--output", type=Path, default=DEFAULT_OUTPUT_DIR, help="Output directory")
    parser.add_argument("--collection-summary", type=Path, default=Path("output/collection_summary.json"), help="Collector metadata JSON")
    parser.add_argument("--skip-wayback", action="store_true", help="Skip history checks after AVAILABLE verification")
    parser.add_argument("--max-wayback", type=int, default=WAYBACK_MAX_REQUESTS, help="Maximum Wayback requests after AVAILABLE verification")
    parser.add_argument("--max-availability", type=int, default=AVAILABILITY_MAX_REQUESTS, help="Maximum RDAP requests for strongest initial candidates")
    parser.add_argument("--availability-timeout", type=float, default=AVAILABILITY_TIMEOUT_SECONDS, help="RDAP timeout per request")
    parser.add_argument("--availability-retries", type=int, default=AVAILABILITY_RETRIES, help="RDAP retries")
    parser.add_argument("--state", type=Path, default=DEFAULT_STATE_PATH, help="Persistent metadata state JSON")
    parser.add_argument("--send-telegram", action="store_true", help="Send the AVAILABLE-only report for a new dataset")
    parser.add_argument("--telegram-test", action="store_true", help="Send a Telegram integration-test message and exit")
    return parser


def main(argv: list[str] | None = None) -> int:
    args = build_parser().parse_args(argv)
    if args.telegram_test:
        sent, message = send_test_message()
        print(message)
        return 0 if sent else 1
    try:
        metadata = _read_collection_metadata(args.collection_summary)
        state = ProcessState(args.state)
        dataset_id = str(metadata.get("dataset_id", ""))
        dataset_date = str(metadata.get("dataset_date", ""))
        dataset_source = str(metadata.get("source", ""))
        evaluations, stats, rejected = run(
            args.input, args.output, args.skip_wayback, max(0, args.max_wayback), state=state,
            max_availability=max(0, args.max_availability), availability_timeout=max(1.0, args.availability_timeout),
            availability_retries=max(0, args.availability_retries), dataset_id=dataset_id, dataset_date=dataset_date, dataset_source=dataset_source,
        )
        if args.send_telegram:
            if dataset_id and state.dataset_was_sent(dataset_id):
                print("Dataset already reported; Telegram AVAILABLE-only report not sent again.")
            else:
                sent, message = send_daily_summary(
                    evaluations,
                    dataset_date=dataset_date,
                    source=dataset_source,
                    dataset_id=dataset_id,
                    counts={
                        "RAW_ROWS": int(metadata.get("collected_lines", 0) or 0),
                        "VALID_COM": int(metadata.get("unique_com_domains", stats["input_rows"]) or stats["input_rows"]),
                        "REJECTED": int(metadata.get("rejected_lines", stats["rejected"]) or stats["rejected"]),
                        "DUPLICATES": int(metadata.get("duplicate_domains", stats["duplicates"]) or stats["duplicates"]),
                        **{key: stats[key.lower()] for key in (AVAILABLE, REGISTERED, PENDING, AUCTION, UNKNOWN)},
                    },
                    quality_candidates=stats["initial_scored"],
                    rdap_checked=stats["availability_checked"],
                )
                print(message)
                if sent and dataset_id:
                    state.mark_dataset_sent(dataset_id, sent_at_utc=datetime.now(timezone.utc).isoformat(), dataset_date=dataset_date, source=dataset_source, top_count=len(evaluations))
        state.save()
    except (OSError, csv.Error, UnicodeError) as exc:
        print(f"Input/output error: {exc}", file=sys.stderr)
        return 2

    print(f"Processed {stats['input_rows']} unique valid .COM rows; quality candidates {stats['initial_scored']}; RDAP checked {stats['availability_checked']}; final candidates {len(evaluations)}.")
    print(f"RDAP selected {stats['rdap_selected']}; deferred {stats['rdap_deferred']}; Wayback requests made {stats['wayback_requests']}.")
    print(f"Statuses — available {stats['available']}; registered {stats['registered']}; pending {stats['pending']}; auction {stats['auction']}; unknown {stats['unknown']}.")
    print(f"Lifecycle filtered {stats['lifecycle_filtered']}; basic-quality filtered {stats['quality_filtered']}; below-threshold after AVAILABLE {stats['final_threshold_rejected']}.")
    print(f"Results written to {args.output / 'results.csv'} and {args.output / 'top_domains.txt'}.")
    if rejected:
        print(f"Rejected input-row details: {len(rejected)}.")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
