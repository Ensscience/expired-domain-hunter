from __future__ import annotations

import csv
import tempfile
import unittest
from pathlib import Path

from hunter import run
from src.availability import AUCTION, AVAILABLE, PENDING, REGISTERED, UNKNOWN, AvailabilityResult
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
        return AvailabilityResult(domain, status, "2026-08-16T00:00:00+00:00", f"https://rdap.example/{domain}", 404 if status == AVAILABLE else 200, status)


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
            evaluations, stats, rejected = run(
                input_path,
                root / "output",
                skip_wayback=True,
                max_wayback=0,
                availability_checker=checker,
                state=ProcessState(root / "state.json"),
                max_availability=1,
            )
            self.assertEqual(rejected, [])
            self.assertEqual(stats["initial_scored"], 3)
            self.assertEqual(stats["rdap_selected"], 1)
            self.assertEqual(stats["rdap_deferred"], 2)
            self.assertEqual(checker.calls, ["smartinvoices.com"])
            self.assertEqual(stats["available"], 1)

    def test_only_available_candidates_enter_quality_and_scoring_pipeline(self):
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
            checker = FakeChecker({
                "strongsoftware.com": AVAILABLE,
                "registeredname.com": REGISTERED,
                "unknownname.com": UNKNOWN,
            })
            state = ProcessState(root / ".state" / "processed.json")
            evaluations, stats, rejected = run(
                input_path,
                root / "output",
                skip_wayback=True,
                max_wayback=0,
                availability_checker=checker,
                state=state,
            )
            self.assertEqual(rejected, [])
            self.assertEqual(stats["available"], 1)
            self.assertEqual(stats["registered"], 1)
            self.assertEqual(stats["pending"], 1)
            self.assertEqual(stats["auction"], 1)
            self.assertEqual(stats["unknown"], 1)
            self.assertNotIn("pendingname.com", checker.calls)
            self.assertNotIn("auctionname.com", checker.calls)
            by_domain = {item.domain: item for item in evaluations}
            self.assertEqual(by_domain["strongsoftware.com"].registration_status, AVAILABLE)
            self.assertEqual(by_domain["strongsoftware.com"].source_count, 2)
            self.assertEqual(by_domain["strongsoftware.com"].sources, "source-a;source-b")
            self.assertEqual(by_domain["registeredname.com"].registration_status, REGISTERED)
            self.assertEqual(by_domain["unknownname.com"].registration_status, UNKNOWN)
            with (root / "output" / "results.csv").open(newline="", encoding="utf-8") as handle:
                rows = list(csv.DictReader(handle))
            self.assertIn("registration_status", rows[0])
            self.assertIn("sources", rows[0])


if __name__ == "__main__":
    unittest.main()
