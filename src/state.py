"""Persistent processed-domain and dataset notification state.

GitHub Actions restores and saves this directory through the workflow cache.
Sent datasets are not reported again, while the existing domain-level status
records continue to support availability cooldowns and retries.
"""

from __future__ import annotations

import json
from datetime import datetime, timedelta, timezone
from pathlib import Path
from typing import Any


class ProcessState:
    def __init__(self, path: str | Path):
        self.path = Path(path)
        self.data: dict[str, Any] = {"version": 2, "domains": {}, "datasets": {}}
        if self.path.exists() and self.path.stat().st_size:
            try:
                loaded = json.loads(self.path.read_text(encoding="utf-8"))
                if isinstance(loaded, dict) and isinstance(loaded.get("domains"), dict):
                    self.data = loaded
                    self.data.setdefault("version", 2)
                    self.data.setdefault("datasets", {})
            except (OSError, ValueError, TypeError):
                self.data = {"version": 2, "domains": {}, "datasets": {}}

    @property
    def domains(self) -> dict[str, dict[str, Any]]:
        return self.data.setdefault("domains", {})

    @property
    def datasets(self) -> dict[str, dict[str, Any]]:
        return self.data.setdefault("datasets", {})

    def get(self, domain: str) -> dict[str, Any] | None:
        value = self.domains.get(domain.lower().strip().rstrip("."))
        return value if isinstance(value, dict) else None

    def was_sent(self, domain: str) -> bool:
        record = self.get(domain)
        return bool(record and record.get("sent"))

    def dataset_record(self, dataset_id: str) -> dict[str, Any] | None:
        value = self.datasets.get(dataset_id)
        return value if isinstance(value, dict) else None

    def dataset_was_sent(self, dataset_id: str) -> bool:
        record = self.dataset_record(dataset_id)
        return bool(record and record.get("sent"))

    def mark_dataset_sent(self, dataset_id: str, *, sent_at_utc: str, dataset_date: str, source: str, top_count: int) -> None:
        self.datasets[dataset_id] = {
            **(self.dataset_record(dataset_id) or {}),
            "sent": True,
            "sent_at_utc": sent_at_utc,
            "dataset_date": dataset_date,
            "source": source,
            "top_count": top_count,
        }

    def should_skip(self, domain: str, now: datetime | None = None) -> bool:
        """Skip recently processed domains, but allow UNKNOWN rechecks sooner."""

        record = self.get(domain)
        if not record:
            return False
        if record.get("sent"):
            return True
        if record.get("registration_status") == "AVAILABLE" and int(record.get("score") or 0) >= 80:
            return False
        checked = record.get("checked_at_utc")
        if not checked:
            return False
        try:
            checked_at = datetime.fromisoformat(str(checked))
        except ValueError:
            return False
        now = now or datetime.now(timezone.utc)
        cooldown_hours = 4 if record.get("registration_status") == "UNKNOWN" else 20
        return now < checked_at + timedelta(hours=cooldown_hours)

    def record(
        self,
        domain: str,
        registration_status: str,
        checked_at_utc: str,
        *,
        sent: bool = False,
        score: int | None = None,
        reason: str = "",
    ) -> None:
        normalized = domain.lower().strip().rstrip(".")
        previous = self.domains.get(normalized, {})
        self.domains[normalized] = {
            **previous,
            "checked_at_utc": checked_at_utc,
            "registration_status": registration_status,
            "sent": bool(sent or previous.get("sent")),
            "score": score,
            "reason": reason,
        }

    def mark_sent(self, domain: str, checked_at_utc: str, score: int, reason: str = "") -> None:
        self.record(domain, "AVAILABLE", checked_at_utc, sent=True, score=score, reason=reason)

    def save(self) -> None:
        self.path.parent.mkdir(parents=True, exist_ok=True)
        temporary = self.path.with_suffix(self.path.suffix + ".tmp")
        temporary.write_text(json.dumps(self.data, indent=2, sort_keys=True) + "\n", encoding="utf-8")
        temporary.replace(self.path)
