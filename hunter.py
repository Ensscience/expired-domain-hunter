#!/usr/bin/env python3
"""Collect, score, rank, enrich, and report the TOP 50 expired/dropped .COM domains."""

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
    "rank",
    "domain",
    "score",
    "classification",
    "availability_status",
    "source",
    "source_count",
    "sources",
    "brandability",
    "commercial_intent",
    "keyword_quality",
    "historical_quality",
    "spam_risk",
    "end_user_potential",
    "potential_industries",
    "reason",
    "wayback_url",
]


def _source_count(candidate: DomainCandidate) -> int:
    try:
        return max(1, int(candidate.extra.get("source_count", 1)))
    except (TypeError, ValueError):
        return 1


def _sources(candidate: DomainCandidate) -> str:
    return str(candidate.extra.get("sources", candidate.source or "unknown"))


def _lifecycle_status(candidate: DomainCandidate) -> str:
    status = candidate.status.lower()
    if "pending" in status or "redemption" in status:
        return PENDING
    if any(marker in status for marker in ("auction", "backorder", "bidding", "aftermarket", "buy now", "expiring", "pre-release", "prerelease")):
        return AUCTION
    return UNKNOWN


def _neutral_history(domain: str) -> HistorySignals:
    return HistorySignals(
        checked=False,
        historical_quality=4.0,
        previous_use="History deferred until after TOP 50 selection.",
        wayback_url=wayback_url(domain),
    )


def _initial_rank_key(item: tuple[DomainCandidate, FilterResult, Evaluation]) -> tuple[int, int, int, int, int, str]:
    candidate, _, evaluation = item
    return (
        -evaluation.score,
        len(candidate.label),
        -evaluation.commercial_intent,
        -evaluation.keyword_quality,
        -evaluation.brandability - evaluation.end_user_potential,
        candidate.normalized_domain,
    )


def _final_rank_key(item: Evaluation) -> tuple[int, int, int, int, int, str]:
    return (
        -item.score,
        len(item.domain.split(".", 1)[0]),
        -item.commercial_intent,
        -item.keyword_quality,
        -item.brandability - item.end_user_potential,
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
        handle.write("🔥 TOP 50 EXPIRED .COM\n")
        handle.write("QUALITY SCORE ≠ AVAILABILITY\n")
        handle.write("Availability labels are RDAP enrichment; UNKNOWN is not AVAILABLE. Verify AVAILABLE at a registrar before registration.\n\n")
        if not evaluations:
            handle.write("No qualifying expired/dropped .COM domains were scored in this dataset.\n")
        else:
            for rank, item in enumerate(evaluations, start=1):
                handle.write(f"{rank}. {item.domain}\n")
                handle.write(f"   Score: {item.score}/100 | Status: {item.registration_status}\n")
                handle.write(f"   Source: {item.sources}\n")
                handle.write(f"   Why: {item.reason}\n")
                if item.wayback_url:
                    handle.write(f"   RDAP/Wayback: {item.wayback_url}\n")
                handle.write("\n")

    summary_path.write_text(json.dumps(summary, indent=2) + "\n", encoding="utf-8")
    return results_path, top_path, summary_path


def _unique_candidates(candidates: list[DomainCandidate]) -> list[DomainCandidate]:
    seen: set[str] = set()
    unique: list[DomainCandidate] = []
    for candidate in candidates:
        if candidate.normalized_domain not in seen:
            seen.add(candidate.normalized_domain)
            unique.append(candidate)
    return unique


def _make_initial_evaluation(candidate: DomainCandidate, filter_result: FilterResult) -> Evaluation:
    item = evaluate(candidate, filter_result, _neutral_history(candidate.normalized_domain))
    item.status = candidate.status
    item.registration_status = UNKNOWN
    item.source_count = _source_count(candidate)
    item.sources = _sources(candidate)
    item.score_stage = "INITIAL"
    item.wayback_url = rdap_url(candidate.normalized_domain)
    item.reason = f"{item.reason} Availability is separate from quality; status is UNKNOWN until RDAP enrichment."
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
    candidates, rejected_rows = load_domains(input_path)
    candidates = _unique_candidates(candidates)
    history_client = WaybackClient(max_requests=max_wayback)
    checker = availability_checker or VerisignRdapChecker(
        max_requests=max_availability,
        timeout=availability_timeout,
        retries=availability_retries,
    )
    stats = {
        "input_rows": len(candidates),
        "rejected": len(rejected_rows),
        "lifecycle_filtered": 0,
        "availability_checked": 0,
        "available": 0,
        "registered": 0,
        "pending": 0,
        "auction": 0,
        "unknown": 0,
        "quality_filtered": 0,
        "initial_scored": 0,
        "top50_count": 0,
        "rdap_selected": 0,
        "rdap_deferred": 0,
        "state_skipped": 0,
        "evaluated": 0,
    }

    # Score all locally acceptable candidates first. Domain-level cooldowns do
    # not suppress a new dataset report; dataset-level state does that later.
    initial_ranked: list[tuple[DomainCandidate, FilterResult, Evaluation]] = []
    for candidate in candidates:
        lifecycle = _lifecycle_status(candidate)
        if candidate.status and lifecycle in {PENDING, AUCTION}:
            stats["lifecycle_filtered"] += 1
            stats[lifecycle.lower()] += 1
            continue

        filter_result = inspect_candidate(candidate)
        if not filter_result.accepted or filter_result.spam_signal:
            stats["quality_filtered"] += 1
            continue

        initial = _make_initial_evaluation(candidate, filter_result)
        initial_ranked.append((candidate, filter_result, initial))
        stats["initial_scored"] += 1

    initial_ranked.sort(key=_initial_rank_key)
    top50 = initial_ranked[:DEFAULT_TOP_N]
    stats["top50_count"] = len(top50)
    selected_for_rdap = top50[: max(0, max_availability)]
    stats["rdap_selected"] = len(selected_for_rdap)
    stats["rdap_deferred"] = max(0, len(top50) - len(selected_for_rdap))

    top_items: dict[str, Evaluation] = {candidate.normalized_domain: initial for candidate, _, initial in top50}
    top_context: dict[str, tuple[DomainCandidate, FilterResult]] = {
        candidate.normalized_domain: (candidate, filter_result) for candidate, filter_result, _ in top50
    }

    for candidate, filter_result, initial in selected_for_rdap:
        registration = checker.check(candidate.normalized_domain)
        stats["availability_checked"] += 1
        status_key = registration.registration_status.lower()
        if status_key in stats:
            stats[status_key] += 1

        if registration.registration_status == AVAILABLE:
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
            stats["evaluated"] += 1
        else:
            item = initial
            item.reason = f"{initial.reason} Availability status: {registration.registration_status}. {registration.reason}"
            item.wayback_url = registration.rdap_url or rdap_url(candidate.normalized_domain)
            item.score_stage = "INITIAL"

        item.status = candidate.status
        item.registration_status = registration.registration_status
        item.source_count = _source_count(candidate)
        item.sources = _sources(candidate)
        top_items[candidate.normalized_domain] = item
        if state:
            state.record(candidate.normalized_domain, registration.registration_status, registration.checked_at_utc, score=item.score, reason=item.reason)

    evaluations = sorted(top_items.values(), key=_final_rank_key)
    summary = {
        "generated_at_utc": datetime.now(timezone.utc).isoformat(),
        "dataset_id": dataset_id,
        "dataset_date": dataset_date,
        "dataset_source": dataset_source,
        "total_input_candidates": len(candidates),
        "initial_scored_candidates": stats["initial_scored"],
        "top50_count": len(evaluations),
        "rdap_selected_candidates": stats["rdap_selected"],
        "rdap_deferred_candidates": stats["rdap_deferred"],
        "availability_requests_made": checker.requests_made,
        "wayback_requests_made": history_client.requests_made,
        "status_counts": {key: sum(item.registration_status == key for item in evaluations) for key in (AVAILABLE, REGISTERED, PENDING, AUCTION, UNKNOWN)},
        "buy_candidates": sum(item.registration_status == AVAILABLE and item.score >= 80 for item in evaluations),
        "selection_strategy": "TOP 50 by initial quality score, then final score where Wayback was available; deterministic component and domain tie-breakers.",
        "scoring_note": "Quality score is separate from availability. AVAILABLE means verify at registrar before registration; UNKNOWN is never described as available.",
    }
    write_results(evaluations, output_dir, summary)
    for status in (AVAILABLE, REGISTERED, PENDING, AUCTION, UNKNOWN):
        stats[status.lower()] = sum(item.registration_status == status for item in evaluations)
    stats["wayback_requests"] = history_client.requests_made
    stats["availability_requests"] = checker.requests_made
    return evaluations, stats, rejected_rows


def _read_collection_metadata(path: Path) -> dict[str, str]:
    if not path.exists() or not path.stat().st_size:
        return {}
    try:
        value = json.loads(path.read_text(encoding="utf-8"))
    except (OSError, ValueError, TypeError):
        return {}
    return value if isinstance(value, dict) else {}


def build_parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(description="Find and report the TOP 50 expired/dropped .COM domains by Hunter quality score.")
    parser.add_argument("--input", type=Path, default=DEFAULT_INPUT_PATH, help="CSV input path")
    parser.add_argument("--output", type=Path, default=DEFAULT_OUTPUT_DIR, help="Output directory")
    parser.add_argument("--collection-summary", type=Path, default=Path("output/collection_summary.json"), help="Collector metadata JSON")
    parser.add_argument("--skip-wayback", action="store_true", help="Skip Wayback history checks")
    parser.add_argument("--max-wayback", type=int, default=WAYBACK_MAX_REQUESTS, help="Maximum Wayback requests")
    parser.add_argument("--max-availability", type=int, default=AVAILABILITY_MAX_REQUESTS, help="Maximum RDAP requests for TOP 50")
    parser.add_argument("--availability-timeout", type=float, default=AVAILABILITY_TIMEOUT_SECONDS, help="RDAP timeout per request")
    parser.add_argument("--availability-retries", type=int, default=AVAILABILITY_RETRIES, help="RDAP retries")
    parser.add_argument("--state", type=Path, default=DEFAULT_STATE_PATH, help="Persistent state JSON")
    parser.add_argument("--send-telegram", action="store_true", help="Send one consolidated TOP 50 report for a new dataset")
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
            args.input,
            args.output,
            args.skip_wayback,
            max(0, args.max_wayback),
            state=state,
            max_availability=max(0, args.max_availability),
            availability_timeout=max(1.0, args.availability_timeout),
            availability_retries=max(0, args.availability_retries),
            dataset_id=dataset_id,
            dataset_date=dataset_date,
            dataset_source=dataset_source,
        )
        if args.send_telegram:
            if dataset_id and state.dataset_was_sent(dataset_id):
                print("Dataset already reported; Telegram TOP 50 not sent again.")
            else:
                sent, message = send_daily_summary(evaluations, dataset_date=dataset_date, source=dataset_source)
                print(message)
                if sent and dataset_id:
                    state.mark_dataset_sent(
                        dataset_id,
                        sent_at_utc=datetime.now(timezone.utc).isoformat(),
                        dataset_date=dataset_date,
                        source=dataset_source,
                        top_count=len(evaluations),
                    )
        state.save()
    except (OSError, csv.Error, UnicodeError) as exc:
        print(f"Input/output error: {exc}", file=sys.stderr)
        return 2

    print(f"Processed {stats['input_rows']} unique valid .COM rows; quality-scored {stats['initial_scored']}; TOP 50 selected {stats['top50_count']}.")
    print(f"RDAP requests made {stats['rdap_selected']}; deferred within TOP 50 {stats['rdap_deferred']}; Wayback requests made {stats['wayback_requests']}.")
    print(f"TOP 50 statuses — available {stats['available']}; registered {stats['registered']}; pending {stats['pending']}; auction {stats['auction']}; unknown {stats['unknown']}.")
    print(f"Basic-quality filtered {stats['quality_filtered']}; lifecycle-filtered {stats['lifecycle_filtered']}.")
    print(f"Results written to {args.output / 'results.csv'} and {args.output / 'top_domains.txt'}.")
    if rejected:
        print(f"Rejected input-row details: {len(rejected)}.")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
