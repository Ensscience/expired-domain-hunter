#!/usr/bin/env python3
"""Run the expired .COM domain hunting pipeline."""

from __future__ import annotations

import argparse
import csv
import json
import sys
from dataclasses import asdict
from datetime import datetime, timezone
from pathlib import Path

from config import DEFAULT_INPUT_PATH, DEFAULT_OUTPUT_DIR, DEFAULT_TOP_N, WAYBACK_MAX_REQUESTS, classification
from src.data_source import DomainCandidate, load_domains
from src.filters import inspect_candidate
from src.history import HistorySignals, WaybackClient, wayback_url
from src.scoring import Evaluation, evaluate
from src.telegram import send_daily_summary, send_test_message

RESULT_COLUMNS = [
    "domain",
    "score",
    "classification",
    "suggested_max_bid",
    "estimated_resale_range",
    "brandability",
    "commercial_intent",
    "keyword_quality",
    "historical_quality",
    "spam_risk",
    "end_user_potential",
    "potential_industries",
    "reason",
    "wayback_url",
    "source",
]


def _evaluation_row(item: Evaluation) -> dict[str, object]:
    return {key: getattr(item, key) for key in RESULT_COLUMNS}


def write_results(evaluations: list[Evaluation], output_dir: Path) -> tuple[Path, Path, Path]:
    output_dir.mkdir(parents=True, exist_ok=True)
    results_path = output_dir / "results.csv"
    top_path = output_dir / "top_domains.txt"
    summary_path = output_dir / "run_summary.json"

    with results_path.open("w", encoding="utf-8", newline="") as handle:
        writer = csv.DictWriter(handle, fieldnames=RESULT_COLUMNS)
        writer.writeheader()
        writer.writerows(_evaluation_row(item) for item in evaluations)

    with top_path.open("w", encoding="utf-8") as handle:
        handle.write("EXPIRED .COM DOMAIN HUNTER — RANKED OPPORTUNITIES\n")
        handle.write("AI estimates are not guaranteed valuations; manually verify availability, history, trademarks, and buyers.\n\n")
        if not evaluations:
            handle.write("No eligible .COM domains were found in the input dataset.\n")
        else:
            for index, item in enumerate(evaluations[:DEFAULT_TOP_N], start=1):
                handle.write(f"{index}. {item.domain} — {item.score}/100 — {item.classification}\n")
                handle.write(f"   Max bid: {item.suggested_max_bid} | Estimated resale: {item.estimated_resale_range}\n")
                handle.write(f"   Industries: {item.potential_industries}\n")
                handle.write(f"   Reason: {item.reason}\n")
                if item.wayback_url:
                    handle.write(f"   Wayback: {item.wayback_url}\n")
                handle.write("\n")

    summary = {
        "generated_at_utc": datetime.now(timezone.utc).isoformat(),
        "total_evaluated": len(evaluations),
        "buy_candidates": sum(item.classification == "BUY CANDIDATE" for item in evaluations),
        "watch_candidates": sum(item.classification == "WATCH" for item in evaluations),
        "ignore_candidates": sum(item.classification == "IGNORE" for item in evaluations),
        "top_domain": evaluations[0].domain if evaluations else None,
        "scoring_note": "AI estimate — manual verification required.",
    }
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


def run(input_path: Path, output_dir: Path, skip_wayback: bool = False, max_wayback: int = WAYBACK_MAX_REQUESTS) -> tuple[list[Evaluation], dict[str, int], list[str]]:
    candidates, rejected_rows = load_domains(input_path)
    candidates = _unique_candidates(candidates)
    history_client = WaybackClient(max_requests=max_wayback)
    evaluations: list[Evaluation] = []
    stats = {"input_rows": len(candidates), "rejected": len(rejected_rows), "filtered": 0, "evaluated": 0}

    for candidate in candidates:
        filter_result = inspect_candidate(candidate)
        if not filter_result.accepted:
            stats["filtered"] += 1
            continue
        if skip_wayback:
            history = HistorySignals(checked=False, wayback_url=wayback_url(candidate.normalized_domain), previous_use="Wayback check skipped for this run.")
        else:
            history = history_client.inspect(candidate.normalized_domain)
        evaluations.append(evaluate(candidate, filter_result, history))
        stats["evaluated"] += 1

    evaluations.sort(key=lambda item: (-item.score, item.domain))
    write_results(evaluations, output_dir)
    stats["wayback_requests"] = history_client.requests_made
    return evaluations, stats, rejected_rows


def build_parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(description="Find and rank potentially valuable expired or dropped .COM domains from CSV input.")
    parser.add_argument("--input", type=Path, default=DEFAULT_INPUT_PATH, help="CSV input path (default: input/domains.csv)")
    parser.add_argument("--output", type=Path, default=DEFAULT_OUTPUT_DIR, help="Output directory (default: output)")
    parser.add_argument("--skip-wayback", action="store_true", help="Skip network history checks for local tests or offline runs")
    parser.add_argument("--max-wayback", type=int, default=WAYBACK_MAX_REQUESTS, help="Maximum Wayback requests per run")
    parser.add_argument("--send-telegram", action="store_true", help="Send one summary when any domain scores 80+")
    parser.add_argument("--telegram-test", action="store_true", help="Send an explicit Telegram integration-test message and exit")
    return parser


def main(argv: list[str] | None = None) -> int:
    args = build_parser().parse_args(argv)
    if args.telegram_test:
        sent, message = send_test_message()
        print(message)
        return 0 if sent else 1
    try:
        evaluations, stats, rejected = run(args.input, args.output, args.skip_wayback, max(0, args.max_wayback))
    except (OSError, csv.Error, UnicodeError) as exc:
        print(f"Input/output error: {exc}", file=sys.stderr)
        return 2

    print(f"Processed {stats['input_rows']} unique valid .COM rows; evaluated {stats['evaluated']}; filtered {stats['filtered']}; rejected {stats['rejected']}.")
    print(f"Wayback requests made: {stats['wayback_requests']}.")
    print(f"Results written to {args.output / 'results.csv'} and {args.output / 'top_domains.txt'}.")
    if rejected:
        print(f"Rejected-row details retained for diagnostics: {len(rejected)}.")
    if args.send_telegram:
        sent, message = send_daily_summary(evaluations)
        print(message)
        if sent:
            print(f"Telegram qualifying-domain count: {sum(item.score >= 80 for item in evaluations)}.")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
