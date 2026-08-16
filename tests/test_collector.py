from __future__ import annotations

import csv
import json
import tempfile
import unittest
from pathlib import Path

import requests

from src.collector import CollectionError, collect_domains


class FakeResponse:
    def __init__(self, text: str):
        self.text = text

    def raise_for_status(self):
        return None


class FakeSession:
    def __init__(self, responses: dict[str, str] | None = None, fail: bool = False):
        self.responses = responses or {}
        self.fail = fail
        self.headers = {}
        self.calls: list[tuple[str, float]] = []

    def get(self, url: str, timeout: float):
        self.calls.append((url, timeout))
        if self.fail:
            raise requests.RequestException("simulated public-feed failure")
        return FakeResponse(self.responses[url])


class CollectorTests(unittest.TestCase):
    def test_collects_com_filters_other_tlds_and_deduplicates(self):
        feeds = {
            "expired": "https://example.test/expired",
            "dropped": "https://example.test/dropped",
        }
        session = FakeSession(
            {
                feeds["expired"]: "alpha.com\nalpha.com\nexample.net\n",
                feeds["dropped"]: "beta.com\nalpha.com\nnot a domain\n",
            }
        )
        with tempfile.TemporaryDirectory() as directory:
            root = Path(directory)
            output = root / "input" / "domains.csv"
            summary = root / "output" / "collection_summary.json"
            result = collect_domains(output, summary, session=session, feeds=feeds, retries=0)
            self.assertEqual(result.collected_lines, 6)
            self.assertEqual(result.expired_com_domains, 2)
            self.assertEqual(result.dropped_com_domains, 2)
            self.assertEqual(result.unique_com_domains, 2)
            self.assertEqual(result.duplicate_domains, 2)
            self.assertEqual(result.rejected_lines, 2)
            self.assertFalse(result.fallback_used)
            self.assertEqual(len(session.calls), 2)
            with output.open(newline="", encoding="utf-8") as handle:
                rows = list(csv.DictReader(handle))
            self.assertEqual(rows, [
                {"domain": "alpha.com", "status": "dropped;expired", "source": "whoisfreaks-free-github:expired+dropped"},
                {"domain": "beta.com", "status": "dropped", "source": "whoisfreaks-free-github:dropped"},
            ])
            self.assertEqual(json.loads(summary.read_text(encoding="utf-8"))["unique_com_domains"], 2)

    def test_feed_failure_preserves_existing_csv_fallback(self):
        feeds = {"expired": "https://example.test/expired", "dropped": "https://example.test/dropped"}
        session = FakeSession(fail=True)
        with tempfile.TemporaryDirectory() as directory:
            root = Path(directory)
            fallback = root / "input" / "domains.csv"
            fallback.parent.mkdir(parents=True)
            fallback.write_text("domain\nkeepme.com\n", encoding="utf-8")
            summary = root / "output" / "collection_summary.json"
            result = collect_domains(fallback, summary, session=session, feeds=feeds, retries=0, fallback_path=fallback)
            self.assertTrue(result.fallback_used)
            self.assertEqual(fallback.read_text(encoding="utf-8"), "domain\nkeepme.com\n")
            self.assertEqual(len(result.feeds), 2)
            self.assertTrue(all(item.status == "error" for item in result.feeds))

    def test_feed_failure_without_fallback_does_not_synthesize_data(self):
        feeds = {"expired": "https://example.test/expired", "dropped": "https://example.test/dropped"}
        with tempfile.TemporaryDirectory() as directory:
            root = Path(directory)
            with self.assertRaises(CollectionError):
                collect_domains(root / "input.csv", root / "summary.json", session=FakeSession(fail=True), feeds=feeds, retries=0, fallback_path=root / "missing.csv")


if __name__ == "__main__":
    unittest.main()
