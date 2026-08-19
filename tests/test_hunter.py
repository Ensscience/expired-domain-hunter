from __future__ import annotations

import csv
import tempfile
import unittest
from pathlib import Path

from hunter import RESULT_COLUMNS, run, write_results
from config import classification
from src.data_source import DomainCandidate, load_domains, parse_rows
from src.filters import inspect_candidate
from src.history import HistorySignals, WaybackClient
from src.scoring import Evaluation, evaluate
from src.telegram import build_summary_for_test, send_daily_summary


class FakeResponse:
    status_code = 200
    headers = {}

    def raise_for_status(self):
        return None

    def json(self):
        return [
            ["timestamp", "original", "statuscode", "mimetype", "digest"],
            ["20120101000000", "http://smartinvoices.com/pricing", "200", "text/html", "a"],
            ["20220101000000", "http://smartinvoices.com/product", "200", "text/html", "b"],
        ]


class FakeSession:
    def __init__(self):
        self.headers = {}
        self.calls = []

    def get(self, endpoint, params, timeout):
        self.calls.append((endpoint, params, timeout))
        return FakeResponse()


class FakeTelegramResponse:
    def raise_for_status(self):
        return None

    def json(self):
        return {"ok": True}


class FakeTelegramSession:
    def __init__(self):
        self.payload = None
        self.payloads = []

    def post(self, endpoint, json, timeout):
        self.payload = (endpoint, json, timeout)
        self.payloads.append(self.payload)
        return FakeTelegramResponse()


def sample_evaluation(score: int = 91) -> Evaluation:
    return Evaluation(
        domain="smartinvoices.com",
        score=score,
        classification="BUY CANDIDATE" if score >= 80 else "WATCH",
        suggested_max_bid="$20",
        estimated_resale_range="$1,000-$3,000",
        brandability=18,
        commercial_intent=19,
        keyword_quality=14,
        length_readability=9,
        historical_quality=9,
        backlink_quality=7,
        age_history=4,
        end_user_potential=10,
        spam_risk="low",
        potential_industries="invoicing and accounts-receivable software",
        reason="Short commercial keyword; AI estimate — manual verification required",
        wayback_url="https://web.archive.org/web/*/smartinvoices.com",
        source="test",
        status="expired",
        registration_status="AVAILABLE",
        source_count=1,
        sources="test",
    )


class HunterTests(unittest.TestCase):
    def test_com_filter_and_optional_columns(self):
        candidates, rejected = parse_rows(
            [
                {"domain": "SmartInvoices.com", "keyword": "invoicing software"},
                {"domain": "example.net", "keyword": "software"},
                {"domain": "bad domain.com", "keyword": ""},
            ]
        )
        self.assertEqual([candidate.domain for candidate in candidates], ["smartinvoices.com"])
        self.assertEqual(len(rejected), 2)
        self.assertIsNone(candidates[0].backlinks)

    def test_spam_and_active_status_filtering(self):
        spam = DomainCandidate(domain="x9x9-casino.com", status="expired")
        active = DomainCandidate(domain="activeproduct.com", status="active")
        self.assertFalse(inspect_candidate(spam).accepted)
        active_result = inspect_candidate(active)
        self.assertFalse(active_result.accepted)
        self.assertTrue(active_result.reasons)
        for status in ("dropped", "deleted", "redemption period", "pending delete", "auction"):
            lifecycle_result = inspect_candidate(DomainCandidate(domain="statuscheck.com", status=status))
            self.assertFalse(lifecycle_result.accepted, status)

    def test_wayback_history_parsing_is_bounded(self):
        session = FakeSession()
        client = WaybackClient(session=session, max_requests=1)
        result = client.inspect("smartinvoices.com")
        self.assertEqual(client.requests_made, 1)
        self.assertEqual(result.snapshots, 2)
        self.assertEqual(result.first_year, 2012)
        self.assertFalse(result.spam_like)
        client.inspect("another.com")
        self.assertEqual(client.requests_made, 1)

    def test_score_and_conservative_valuation(self):
        candidate = DomainCandidate(
            domain="smartinvoices.com",
            status="expired",
            backlinks=420,
            ref_domains=58,
            domain_age=12,
            archive_year=2012,
            keyword="invoicing software",
            search_volume=5400,
        )
        filters = inspect_candidate(candidate)
        history = HistorySignals(
            checked=True,
            snapshots=2,
            first_year=2012,
            last_year=2022,
            historical_quality=9,
            previous_use="Historical URL signals include commercial themes: pricing, product.",
            wayback_url="https://web.archive.org/web/*/smartinvoices.com",
        )
        result = evaluate(candidate, filters, history)
        self.assertGreaterEqual(result.score, 65)
        self.assertIn(result.classification, {"EXCEPTIONAL", "STRONG", "GOOD", "WATCH", "WEAK", "IGNORE"})
        self.assertTrue(result.suggested_max_bid.startswith("$"))
        self.assertIn("manual verification", result.reason)
        self.assertLessEqual(float(result.suggested_max_bid.replace("$", "").replace(",", "")), 250)

    def test_investor_quality_regression_matrix(self):
        strong_one_word = evaluate(DomainCandidate(domain="balustrade.com", status="expired"), inspect_candidate(DomainCandidate(domain="balustrade.com", status="expired")), HistorySignals(checked=False, historical_quality=4.0))
        self.assertGreaterEqual(strong_one_word.score, 60)
        self.assertNotIn("unrecognized", strong_one_word.main_weakness)

        strong_two_word = evaluate(DomainCandidate(domain="cloudledger.com", status="expired"), inspect_candidate(DomainCandidate(domain="cloudledger.com", status="expired")), HistorySignals(checked=False, historical_quality=4.0))
        self.assertGreaterEqual(strong_two_word.score, 60)
        self.assertIn("Natural two-word", strong_two_word.main_strength)

        natural_product = evaluate(DomainCandidate(domain="steelbalustrade.com", status="expired"), inspect_candidate(DomainCandidate(domain="steelbalustrade.com", status="expired")), HistorySignals(checked=False, historical_quality=4.0))
        self.assertGreaterEqual(natural_product.score, 55)

        uncommon_valid = evaluate(DomainCandidate(domain="balustrade.com", status="expired"), inspect_candidate(DomainCandidate(domain="balustrade.com", status="expired")), HistorySignals(checked=False, historical_quality=4.0))
        self.assertNotIn("unrecognized", uncommon_valid.main_weakness)

        brandable = evaluate(DomainCandidate(domain="securelium.com", status="expired"), inspect_candidate(DomainCandidate(domain="securelium.com", status="expired")), HistorySignals(checked=False, historical_quality=4.0))
        self.assertIn("coined", brandable.main_strength)
        self.assertLess(brandable.score, 50)

        for domain in ("payacel.com", "paydoshop.com", "shopicontech.com", "qzxvptk.com", "paymentshealthcenter.com"):
            candidate = DomainCandidate(domain=domain, status="expired")
            result = evaluate(candidate, inspect_candidate(candidate), HistorySignals(checked=False, historical_quality=4.0))
            self.assertLess(result.score, 50, domain)

    def test_classification_bands_and_investor_examples(self):
        self.assertEqual(classification(95), "EXCEPTIONAL")
        self.assertEqual(classification(85), "STRONG")
        self.assertEqual(classification(75), "GOOD")
        self.assertEqual(classification(65), "WATCH")
        self.assertEqual(classification(55), "WEAK")
        self.assertEqual(classification(49), "IGNORE")
        for domain in ("cloudpay.com", "fastmoney.com"):
            candidate = DomainCandidate(domain=domain, status="expired")
            result = evaluate(candidate, inspect_candidate(candidate), HistorySignals(checked=False, historical_quality=4.0))
            self.assertGreaterEqual(result.score, 55)
        for domain in ("vismaweb.com", "webullotc.com"):
            candidate = DomainCandidate(domain=domain, status="expired")
            result = evaluate(candidate, inspect_candidate(candidate), HistorySignals(checked=False, historical_quality=4.0))
            self.assertIn("Trademark risk", result.trademark_risk_flag)
            self.assertLess(result.score, 50)

    def test_generic_suffix_regression_names_are_not_inflated(self):
        weak_names = ["paydoshop.com", "payupshop.com", "shopicontech.com", "datakudi.com", "ukrantech.com", "wojodtech.com", "yaorashop.com"]
        scores = []
        for domain in weak_names:
            candidate = DomainCandidate(domain=domain, status="expired")
            result = evaluate(candidate, inspect_candidate(candidate), HistorySignals(checked=False, historical_quality=4.0))
            scores.append(result.score)
            self.assertLess(result.score, 60, domain)
            self.assertIn(result.classification, {"WEAK", "IGNORE"})
        self.assertLess(max(scores), 60)

    def test_empty_dataset_and_output_schema(self):
        with tempfile.TemporaryDirectory() as directory:
            root = Path(directory)
            empty = root / "empty.csv"
            empty.write_text("domain,keyword\n", encoding="utf-8")
            evaluations, stats, rejected = run(empty, root / "output", skip_wayback=True)
            self.assertEqual(evaluations, [])
            self.assertEqual(stats["evaluated"], 0)
            self.assertEqual(rejected, [])
            with (root / "output" / "results.csv").open(newline="", encoding="utf-8") as handle:
                self.assertEqual(next(csv.reader(handle)), RESULT_COLUMNS)

    def test_telegram_sends_one_consolidated_top50_summary(self):
        session = FakeTelegramSession()
        sent, message = send_daily_summary(
            [sample_evaluation(91), sample_evaluation(70)],
            bot_token="test-token",
            chat_id="test-chat",
            session=session,
        )
        self.assertTrue(sent)
        self.assertIn("TOP 50 EXPIRED .COM", session.payload[1]["text"])
        self.assertIn("QUALITY SCORE ≠ AVAILABILITY", session.payload[1]["text"])
        self.assertEqual(session.payload[1]["text"].count("smartinvoices.com"), 2)
        self.assertEqual(message, "Telegram TOP 50 report sent in 1 message(s).")
        sent_empty, empty_message = send_daily_summary([], bot_token="test-token", chat_id="test-chat", session=session)
        self.assertTrue(sent_empty)
        self.assertIn("TOP 50 EXPIRED .COM", session.payload[1]["text"])
        self.assertEqual(empty_message, "Telegram TOP 50 report sent in 1 message(s).")

    def test_50_entry_report_is_split_without_losing_entries(self):
        session = FakeTelegramSession()
        entries = [sample_evaluation(50 + (index % 45)) for index in range(50)]
        for index, item in enumerate(entries, start=1):
            item.domain = f"candidate{index}.com"
        sent, message = send_daily_summary(entries, bot_token="test-token", chat_id="test-chat", session=session, dataset_date="2026-08-19", source="test-source")
        self.assertTrue(sent)
        self.assertGreater(len(session.payloads), 1)
        combined = "\n".join(payload[1]["text"] for payload in session.payloads)
        self.assertEqual(sum(combined.count(f"candidate{index}.com") for index in range(1, 51)), 50)
        self.assertIn("2026-08-19", combined)
        self.assertEqual(message, f"Telegram TOP 50 report sent in {len(session.payloads)} message(s).")

    def test_summary_contains_manual_verification_label(self):
        self.assertIn("manual verification required", build_summary_for_test([sample_evaluation()]))


if __name__ == "__main__":
    unittest.main()
