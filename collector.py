#!/usr/bin/env python3
"""Fetch the latest legitimate public expired/dropped domain feed."""

from __future__ import annotations

import argparse
import json
import sys
from pathlib import Path

from src.collector import CollectionError, collect_domains


# Keep the checked-in CSV import path as the manual fallback.
def build_parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(description="Collect current expired and dropped domains from public free feeds.")
    parser.add_argument("--output", type=Path, default=Path("input/domains.csv"), help="Normalized CSV output path")
    parser.add_argument("--summary", type=Path, default=Path("output/collection_summary.json"), help="Collection summary JSON path")
    parser.add_argument("--fallback", type=Path, default=Path("input/domains.csv"), help="Existing CSV to keep if all public feeds fail")
    parser.add_argument("--timeout", type=float, default=30.0, help="Per-feed HTTP timeout in seconds")
    parser.add_argument("--retries", type=int, default=2, help="Retries per feed after the first attempt")
    return parser


def main(argv: list[str] | None = None) -> int:
    args = build_parser().parse_args(argv)
    try:
        result = collect_domains(
            output_path=args.output,
            summary_path=args.summary,
            timeout=max(1.0, args.timeout),
            retries=max(0, args.retries),
            fallback_path=args.fallback,
        )
    except CollectionError as exc:
        print(f"Automatic collection failed: {exc}", file=sys.stderr)
        return 2
    print(json.dumps({
        "source": result.source,
        "fallback_used": result.fallback_used,
        "collected_lines": result.collected_lines,
        "unique_com_domains": result.unique_com_domains,
        "expired_com_domains": result.expired_com_domains,
        "dropped_com_domains": result.dropped_com_domains,
        "duplicate_domains": result.duplicate_domains,
        "rejected_lines": result.rejected_lines,
        "summary_path": str(args.summary),
    }, indent=2))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
