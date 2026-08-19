from __future__ import annotations

import tempfile
import unittest
from datetime import datetime, timedelta, timezone
from pathlib import Path

from src.state import ProcessState


class StateTests(unittest.TestCase):
    def test_sent_domain_is_never_reprocessed(self):
        with tempfile.TemporaryDirectory() as directory:
            state = ProcessState(Path(directory) / "state.json")
            now = datetime.now(timezone.utc).isoformat()
            state.mark_sent("example.com", now, 91)
            state.save()
            restored = ProcessState(Path(directory) / "state.json")
            self.assertTrue(restored.was_sent("example.com"))
            self.assertTrue(restored.should_skip("example.com"))

    def test_unknown_domain_rechecks_after_cooldown(self):
        with tempfile.TemporaryDirectory() as directory:
            state = ProcessState(Path(directory) / "state.json")
            checked = (datetime.now(timezone.utc) - timedelta(hours=5)).isoformat()
            state.record("unknown.com", "UNKNOWN", checked, score=0, reason="timeout")
            self.assertFalse(state.should_skip("unknown.com"))

    def test_dataset_report_is_suppressed_by_fingerprint(self):
        with tempfile.TemporaryDirectory() as directory:
            state_path = Path(directory) / "state.json"
            state = ProcessState(state_path)
            state.mark_dataset_sent("feeds:abc", sent_at_utc=datetime.now(timezone.utc).isoformat(), dataset_date="2026-08-16", source="source-a", top_count=50)
            state.save()
            restored = ProcessState(state_path)
            self.assertTrue(restored.dataset_was_sent("feeds:abc"))
            self.assertFalse(restored.dataset_was_sent("feeds:def"))
            self.assertEqual(restored.dataset_record("feeds:abc")["top_count"], 50)

    def test_recent_non_unknown_domain_is_skipped(self):
        with tempfile.TemporaryDirectory() as directory:
            state = ProcessState(Path(directory) / "state.json")
            checked = datetime.now(timezone.utc).isoformat()
            state.record("registered.com", "REGISTERED", checked, score=0)
            self.assertTrue(state.should_skip("registered.com"))


if __name__ == "__main__":
    unittest.main()
