#!/usr/bin/env python3
"""Run the expired/dropped .COM hand-registration hunting pipeline."""

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
    "domain",
    "status",
    "registration_status",
    "source",
    "score",
    "classification",
    "suggested_max_bid",
    "estimated_resale_range",
    "source_count",
    "sources",
    "brandability",
    "commercial_intent",
    "keyword_quality",
    "historical_quality",
    "spam_risk",
    "end_user_potential",
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


def _excluded_evaluation(candidate: DomainCandidate, registration: AvailabilityResult, reason: str, spam_risk: str = "n/a") -> Evaluation:
    return Evaluation(
        domain=candidate.normalized_domain,
        score=0,
        classification="IGNORE",
        suggested_max_bid="$0",
        estimated_resale_range="$0-$0",
        brandability=0,
        commercial_intent=0,
        keyword_quality=0,
        length_readability=0,
        historical_quality=0,
        backlink_quality=0,
        age_history=0,
        end_user_potential=0,
        spam_risk=spam_risk,
        potential_industries="No clear industry signal",
        reason=reason,
        wayback_url=registration.rdap_url or wayback_url(candidate.normalized_domain),
        source=candidate.source,
        status=candidate.status,
        registration_status=registration.registration_status,
        source_count=_source_count(candidate),
        sources=_sources(candidate),
    )


def _lifecycle_status(candidate: DomainCandidate) -> str:
    status = candidate.status.lower()
    if "pending" in status or "redemption" in status:
        return PENDING
    if any(marker in status for marker in ("auction", "backorder", "bidding", "aftermarket", "buy now", "expiring", "pre-release", "prerelease")):
        return AUCTION
    return UNKNOWN


def _neutral_history(domain: str) -> HistorySignals:
    """Provide a no-network baseline for the initial ranking stage."""

    return HistorySignals(
        checked=False,
        historical_quality=4.0,
        previous_use="History deferred until after availability verification.",
        wayback_url=wayback_url(domain),
    )


def _initial_rank_key(item: tuple[DomainCandidate, FilterResult, Evaluation]) -> tuple[int, int, int, int, int, str]:
    candidate, _, evaluation = item
    label_length = len(candidate.label)
    # Initial score is primary; the component tie-breakers make the quality
    # preference explicit and prevent source/file order from deciding RDAP use.
    return (
        -evaluation.score,
        label_length,
        -evaluation.commercial_intent,
        -evaluation.keyword_quality,
        -evaluation.brandability - evaluation.end_user_potential,
        candidate.normalized_domain,
    )


def _evaluation_row(item: Evaluation) -> dict[str, object]:
    return {key: getattr(item, key) for key in RESULT_COLUMNS}


def write_results(evaluations: list[Evaluation], output_dir: Path, summary: dict[str, object]) -> tuple[Path, Path, Path]:
    output_dir.mkdir(parents=True, exist_ok=True)
    results_path = output_dir / "results.csv"
    top_path = output_dir / "top_domains.txt"
    summary_path = output_dir / "run_summary.json"

    with results_path.open("w", encoding="utf-8", newline="") as handle:
        writer = csv.DictWriter(handle, fieldnames=RESULT_COLUMNS)
        writer.writeheader()
        writer.writerows(_evaluation_row(item) for item in evaluations)

    with top_path.open("w", encoding="utf-8") as handle:
        handle.write("HAND-REG .COM DOMAIN HUNTER — RANKED RESULTS\n")
        handle.write("Only AVAILABLE domains can become BUY CANDIDATE alerts. AI estimates require manual verification.\n\n")
        if not evaluations:
            handle.write("No RDAP-selected candidates were processed in this run.\n")
        else:
            for index, item in enumerate(evaluations[:DEFAULT_TOP_N], start=1):
                handle.write(f"{index}. {item.domain} — {item.score}/100 — {item.classification}\n")
                handle.write(f"   Status: {item.registration_status} | Source status: {item.status}\n")
                handle.write(f"   Max bid: {item.suggested_max_bid} | Estimated resale: {item.estimated_resale_range}\n")
                handle.write(f"   Sources ({item.source_count}): {item.sources}\n")
                handle.write(f"   Reason: {item.reason}\n")
                if item.wayback_url:
                    handle.write(f"   Wayback/RDAP: {item.wayback_url}\n")
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
) -> tuple[list[Evaluation], dict[str, int], list[str]]:
    candidates, rejected_rows = load_domains(input_path)
    candidates = _unique_candidates(candidates)
    history_client = WaybackClient(max_requests=max_wayback)
    checker = availability_checker or VerisignRdapChecker(
        max_requests=max_availability,
        timeout=availability_timeout,
        retries=availability_retries,
    )
    evaluations: list[Evaluation] = []
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
        "rdap_selected": 0,
        "rdap_deferred": 0,
        "state_skipped": 0,
        "evaluated": 0,
    }

    # Phase 1: cheap local filtering and neutral-history initial scoring.
    initial_ranked: list[tuple[DomainCandidate, FilterResult, Evaluation]] = []
    for candidate in candidates:
        if state and state.was_sent(candidate.normalized_domain):
            stats["state_skipped"] += 1
            continue

        lifecycle = _lifecycle_status(candidate)
        if candidate.status and lifecycle in {PENDING, AUCTION}:
            stats["lifecycle_filtered"] += 1
            stats[lifecycle.lower()] += 1
            registration = AvailabilityResult(
                candidate.normalized_domain,
                lifecycle,
                datetime.now(timezone.utc).isoformat(),
                rdap_url(candidate.normalized_domain),
                reason="source lifecycle is excluded from hand registration",
            )
            evaluations.append(_excluded_evaluation(candidate, registration, f"Excluded source lifecycle: {candidate.status}"))
            if state:
                state.record(candidate.normalized_domain, lifecycle, registration.checked_at_utc, score=0, reason=registration.reason)
            continue

        if state and state.should_skip(candidate.normalized_domain):
            stats["state_skipped"] += 1
            continue

        filter_result = inspect_candidate(candidate)
        if not filter_result.accepted or filter_result.spam_signal:
            stats["quality_filtered"] += 1
            registration = AvailabilityResult(
                candidate.normalized_domain,
                UNKNOWN,
                datetime.now(timezone.utc).isoformat(),
                rdap_url(candidate.normalized_domain),
                reason="basic local quality/spam filter; RDAP not attempted",
            )
            if state:
                state.record(candidate.normalized_domain, UNKNOWN, registration.checked_at_utc, score=0, reason=registration.reason)
            continue

        initial = evaluate(candidate, filter_result, _neutral_history(candidate.normalized_domain))
        initial.status = candidate.status
        initial.registration_status = UNKNOWN
        initial.source_count = _source_count(candidate)
        initial.sources = _sources(candidate)
        initial.score_stage = "INITIAL"
        initial_ranked.append((candidate, filter_result, initial))
        stats["initial_scored"] += 1

    initial_ranked.sort(key=_initial_rank_key)
    selected = initial_ranked[: max(0, max_availability)]
    stats["rdap_selected"] = len(selected)
    stats["rdap_deferred"] = max(0, len(initial_ranked) - len(selected))

    # Phase 2: only the strongest initial candidates consume RDAP.
    for candidate, filter_result, initial in selected:
        registration = checker.check(candidate.normalized_domain)
        stats["availability_checked"] += 1
        key = registration.registration_status.lower()
        if key in stats:
            stats[key] += 1
        if registration.registration_status != AVAILABLE:
            evaluations.append(_excluded_evaluation(candidate, registration, f"Not eligible for hand registration: {registration.registration_status}. {registration.reason}"))
            if state:
                state.record(candidate.normalized_domain, registration.registration_status, registration.checked_at_utc, score=initial.score, reason=registration.reason)
            continue

        # Phase 3: only AVAILABLE candidates consume Wayback/history budget and
        # receive the final score used for BUY CANDIDATE and Telegram decisions.
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
        item.status = candidate.status
        item.registration_status = AVAILABLE
        item.source_count = _source_count(candidate)
        item.sources = _sources(candidate)
        item.score_stage = "FINAL"
        evaluations.append(item)
        stats["evaluated"] += 1
        if state:
            state.record(candidate.normalized_domain, AVAILABLE, registration.checked_at_utc, score=item.score, reason=item.reason)

    evaluations.sort(key=lambda item: (-item.score, item.domain))
    summary = {
        "generated_at_utc": datetime.now(timezone.utc).isoformat(),
        "total_input_candidates": len(candidates),
        "initial_scored_candidates": stats["initial_scored"],
        "rdap_selected_candidates": stats["rdap_selected"],
        "rdap_deferred_candidates": stats["rdap_deferred"],
        "availability_requests_made": checker.requests_made,
        "wayback_requests_made": history_client.requests_made,
        "status_counts": {key: stats[key] for key in ("available", "registered", "pending", "auction", "unknown")},
        "buy_candidates": sum(item.registration_status == AVAILABLE and item.score >= 80 for item in evaluations),
        "watch_candidates": sum(item.registration_status == AVAILABLE and 65 <= item.score < 80 for item in evaluations),
        "ignore_candidates": sum(item.classification == "IGNORE" for item in evaluations),
        "selection_strategy": "Initial score descending, then short label, commercial intent, keyword quality, brandability plus end-user potential, then domain name.",
        "scoring_note": "AI estimate — manual verification required.",
    }
    write_results(evaluations, output_dir, summary)
    stats["wayback_requests"] = history_client.requests_made
    stats["availability_requests"] = checker.requests_made
    return evaluations, stats, rejected_rows


def build_parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(description="Find expired/dropped .COM domains currently available for hand registration.")
    parser.add_argument("--input", type=Path, default=DEFAULT_INPUT_PATH, help="CSV input path")
    parser.add_argument("--output", type=Path, default=DEFAULT_OUTPUT_DIR, help="Output directory")
    parser.add_argument("--skip-wayback", action="store_true", help="Skip Wayback history checks")
    parser.add_argument("--max-wayback", type=int, default=WAYBACK_MAX_REQUESTS, help="Maximum Wayback requests")
    parser.add_argument("--max-availability", type=int, default=AVAILABILITY_MAX_REQUESTS, help="Maximum RDAP requests")
    parser.add_argument("--availability-timeout", type=float, default=AVAILABILITY_TIMEOUT_SECONDS, help="RDAP timeout per request")
    parser.add_argument("--availability-retries", type=int, default=AVAILABILITY_RETRIES, help="RDAP retries")
    parser.add_argument("--state", type=Path, default=DEFAULT_STATE_PATH, help="Persistent processed-domain state JSON")
    parser.add_argument("--send-telegram", action="store_true", help="Send one summary for new AVAILABLE scores 80+")
    parser.add_argument("--telegram-test", action="store_true", help="Send a Telegram integration-test message and exit")
    return parser


def main(argv: list[str] | None = None) -> int:
    args = build_parser().parse_args(argv)
    if args.telegram_test:
        sent, message = send_test_message()
        print(message)
        return 0 if sent else 1
    try:
        state = ProcessState(args.state)
        evaluations, stats, rejected = run(
            args.input,
            args.output,
            args.skip_wayback,
            max(0, args.max_wayback),
            state=state,
            max_availability=max(0, args.max_availability),
            availability_timeout=max(1.0, args.availability_timeout),
            availability_retries=max(0, args.availability_retries),
        )
        if args.send_telegram:
            sent, message = send_daily_summary(evaluations)
            print(message)
            if sent:
                for item in evaluations:
                    if item.registration_status == AVAILABLE and item.score >= 80:
                        state.mark_sent(item.domain, datetime.now(timezone.utc).isoformat(), item.score, item.reason)
        state.save()
    except (OSError, csv.Error, UnicodeError) as exc:
        print(f"Input/output error: {exc}", file=sys.stderr)
        return 2

    print(f"Processed {stats['input_rows']} unique valid .COM rows; initially scored {stats['initial_scored']}; RDAP-selected {stats['rdap_selected']}; deferred {stats['rdap_deferred']}.")
    print(f"Availability results — available {stats['available']}; registered {stats['registered']}; pending {stats['pending']}; auction {stats['auction']}; unknown {stats['unknown']}.")
    print(f"Basic-quality filtered {stats['quality_filtered']}; lifecycle-filtered {stats['lifecycle_filtered']}; state-skipped {stats['state_skipped']}.")
    print(f"Availability requests made: {stats['availability_requests']}; Wayback requests made: {stats['wayback_requests']}.")
    print(f"Results written to {args.output / 'results.csv'} and {args.output / 'top_domains.txt'}.")
    if rejected:
        print(f"Rejected input-row details: {len(rejected)}.")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
