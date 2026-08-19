from __future__ import annotations

import csv
import tempfile
import unittest
from pathlib import Path
from unittest.mock import patch

from hunter import run
from src.availability import AUCTION, AVAILABLE, PENDING, REGISTERED, UNKNOWN, AvailabilityResult
from src.history import HistorySignals
from src.state import ProcessState


class FakeChecker:
    def __init__(self, statuses):
        self.statuses = statuses
        self.requests_made = 0
        self.calls = []

    def check(self, domain):
        self.calls.append(domain)
        self.requests_made += 1
        status = self.statuses.get(domain, UNKNOWN)
        return AvailabilityResult(domain, status, "2026-08-19T00:00:00+00:00", f"https://rdap.example/{domain}", 404 if status == AVAILABLE else 200, status)


class FakeHistoryClient:
    instances = []

    def __init__(self, max_requests=0):
        self.max_requests = max_requests
        self.requests_made = 0
        self.calls = []
        self.__class__.instances.append(self)

    def inspect(self, domain):
        self.calls.append(domain)
        self.requests_made += 1
        return HistorySignals(checked=True, historical_quality=8.0, snapshots=1, previous_use="Historical commercial use.", wayback_url=f"https://web.archive.org/web/*/{domain}")


class PipelineTests(unittest.TestCase):
    def test_rdap_budget_selects_best_initial_score_not_input_order(self):
        with tempfile.TemporaryDirectory() as directory:
            root = Path(directory)
            input_path = root / "domains.csv"
            input_path.write_text(
                "domain,status,source\n"
                "qzxvnr.com,expired,source-a\n"
                "smartinvoices.com,expired,source-a\n"
                "longuncommercialnameexample.com,expired,source-a\n",
                encoding="utf-8",
            )
            checker = FakeChecker({"smartinvoices.com": AVAILABLE})
            evaluations, stats, rejected = run(input_path, root / "output", skip_wayback=True, max_wayback=0, availability_checker=checker, state=ProcessState(root / "state.json"), max_availability=1)
            self.assertEqual(rejected, [])
            self.assertEqual(stats["initial_scored"], 3)
            self.assertEqual(stats["rdap_selected"], 1)
            self.assertEqual(stats["rdap_deferred"], 2)
            self.assertEqual(checker.calls, ["smartinvoices.com"])
            self.assertEqual(stats["available"], 1)
            self.assertEqual([item.domain for item in evaluations], ["smartinvoices.com"])

    def test_unknown_is_not_final_and_budget_deferred_is_not_classified(self):
        with tempfile.TemporaryDirectory() as directory:
            root = Path(directory)
            input_path = root / "domains.csv"
            input_path.write_text("domain,status,source\nsmartinvoices.com,expired,source-a\ncloudledger.com,expired,source-a\n", encoding="utf-8")
            checker = FakeChecker({})
            evaluations, stats, rejected = run(input_path, root / "output", skip_wayback=True, availability_checker=checker, state=ProcessState(root / "state.json"), max_availability=0)
            self.assertEqual(rejected, [])
            self.assertEqual(evaluations, [])
            self.assertEqual(stats["rdap_selected"], 0)
            self.assertEqual(stats["rdap_deferred"], 2)
            self.assertEqual(stats["unknown"], 0)
            self.assertEqual(checker.calls, [])
            summary = (root / "output" / "run_summary.json").read_text(encoding="utf-8")
            self.assertIn('"final_candidates": 0', summary)

    def test_only_available_candidates_enter_final_ranking(self):
        with tempfile.TemporaryDirectory() as directory:
            root = Path(directory)
            input_path = root / "domains.csv"
            input_path.write_text(
                "domain,status,source,source_count,sources\n"
                "strongsoftware.com,expired,source-a,2,source-a;source-b\n"
                "registeredname.com,expired,source-a,1,source-a\n"
                "pendingname.com,pending delete,source-a,1,source-a\n"
                "auctionname.com,auction,source-a,1,source-a\n"
                "unknownname.com,expired,source-a,1,source-a\n",
                encoding="utf-8",
            )
            checker = FakeChecker({"strongsoftware.com": AVAILABLE, "registeredname.com": REGISTERED, "unknownname.com": UNKNOWN})
            evaluations, stats, rejected = run(input_path, root / "output", skip_wayback=True, max_wayback=0, availability_checker=checker, state=ProcessState(root / ".state" / "processed.json"))
            self.assertEqual(rejected, [])
            self.assertEqual(stats["available"], 1)
            self.assertEqual(stats["registered"], 1)
            self.assertEqual(stats["pending"], 1)
            self.assertEqual(stats["auction"], 1)
            self.assertEqual(stats["lifecycle_filtered"], 2)
            self.assertEqual(stats["unknown"], 1)
            self.assertNotIn("pendingname.com", checker.calls)
            self.assertNotIn("auctionname.com", checker.calls)
            self.assertEqual([item.domain for item in evaluations], ["strongsoftware.com"])
            self.assertEqual(evaluations[0].registration_status, AVAILABLE)
            self.assertEqual(evaluations[0].source_count, 2)
            self.assertEqual(evaluations[0].sources, "source-a;source-b")
            with (root / "output" / "results.csv").open(newline="", encoding="utf-8") as handle:
                rows = list(csv.DictReader(handle))
            self.assertEqual(len(rows), 1)
            self.assertEqual(rows[0]["availability_status"], AVAILABLE)
            self.assertEqual(rows[0]["score_scale"], "0-10")

    def test_available_below_7_is_rejected_and_registered_high_score_is_rejected(self):
        with tempfile.TemporaryDirectory() as directory:
            root = Path(directory)
            input_path = root / "domains.csv"
            input_path.write_text(
                "domain,status,source\n"
                "finance.com,expired,source-a\n"
                "balustrade.com,expired,source-a\n",
                encoding="utf-8",
            )
            checker = FakeChecker({"finance.com": REGISTERED, "balustrade.com": AVAILABLE})
            evaluations, stats, _ = run(input_path, root / "output", skip_wayback=True, availability_checker=checker, state=ProcessState(root / "state.json"), max_availability=2)
            self.assertEqual(evaluations, [])
            self.assertEqual(stats["registered"], 1)
            self.assertEqual(stats["available"], 1)
            self.assertEqual(stats["final_threshold_rejected"], 1)

    def test_wayback_is_called_only_after_available(self):
        FakeHistoryClient.instances = []
        with tempfile.TemporaryDirectory() as directory:
            root = Path(directory)
            input_path = root / "domains.csv"
            input_path.write_text("domain,status,source\nfinance.com,expired,source-a\nsmartinvoices.com,expired,source-a\n", encoding="utf-8")
            checker = FakeChecker({"finance.com": REGISTERED, "smartinvoices.com": AVAILABLE})
            with patch("hunter.WaybackClient", FakeHistoryClient):
                evaluations, stats, _ = run(input_path, root / "output", skip_wayback=False, max_wayback=5, availability_checker=checker, state=ProcessState(root / "state.json"), max_availability=2)
            self.assertEqual([item.domain for item in evaluations], ["smartinvoices.com"])
            self.assertEqual(FakeHistoryClient.instances[0].calls, ["smartinvoices.com"])
            self.assertEqual(stats["wayback_requests"], 1)


if __name__ == "__main__":
    unittest.main()
