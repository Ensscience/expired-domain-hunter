"""Telegram delivery for consolidated TOP 50 expired-domain reports."""

from __future__ import annotations

import os
from typing import Iterable

import requests

from src.scoring import Evaluation

TELEGRAM_MESSAGE_LIMIT = 4096


def _status_label(status: str) -> str:
    normalized = str(status or "UNKNOWN").upper()
    if normalized == "AVAILABLE":
        return "🟢 AVAILABLE — verify at registrar before registration."
    if normalized == "REGISTERED":
        return "🔴 REGISTERED"
    if normalized == "PENDING":
        return "🟡 PENDING"
    if normalized == "AUCTION":
        return "🔴 AUCTION"
    return "🟡 UNKNOWN"


def _entry(index: int, item: Evaluation) -> str:
    return "\n".join(
        [
            f"{index}. {item.domain}",
            f"Score: {item.score}/100",
            f"Status: {_status_label(getattr(item, 'registration_status', 'UNKNOWN'))}",
            f"Source: {getattr(item, 'sources', '') or getattr(item, 'source', 'unknown')}",
            f"Why: {item.reason}",
        ]
    )


def _summary_messages(
    evaluations: Iterable[Evaluation],
    dataset_date: str = "",
    source: str = "",
) -> list[str]:
    ordered = list(evaluations)[:50]
    date_value = dataset_date or "unknown"
    source_value = source or "public expired/dropped feeds"
    prefix = "🔥 TOP 50 EXPIRED .COM\n\n"
    prefix += f"Source/date: {source_value} / {date_value}\n"
    prefix += "QUALITY SCORE ≠ AVAILABILITY\n"
    prefix += "RDAP is status enrichment. UNKNOWN is not AVAILABLE.\n\n"
    if not ordered:
        return [prefix + "No qualifying expired/dropped .COM domains were scored in this dataset."]

    messages: list[str] = []
    current = prefix
    for index, item in enumerate(ordered, start=1):
        block = _entry(index, item)
        candidate = current + block + "\n\n"
        if len(candidate) > TELEGRAM_MESSAGE_LIMIT and current != prefix:
            messages.append(current.rstrip())
            current = prefix + block + "\n\n"
        else:
            current = candidate
    if current.strip():
        messages.append(current.rstrip())
    return messages


def send_daily_summary(
    evaluations: Iterable[Evaluation],
    bot_token: str | None = None,
    chat_id: str | None = None,
    session: requests.Session | None = None,
    *,
    dataset_date: str = "",
    source: str = "",
) -> tuple[bool, str]:
    """Send one consolidated TOP 50 report for a newly detected dataset."""

    bot_token = bot_token or os.getenv("TELEGRAM_BOT_TOKEN", "")
    chat_id = chat_id or os.getenv("TELEGRAM_CHAT_ID", "")
    if not bot_token or not chat_id:
        return False, "Telegram credentials are not configured; TOP 50 report not sent."

    endpoint = f"https://api.telegram.org/bot{bot_token}/sendMessage"
    client = session or requests.Session()
    try:
        messages = _summary_messages(evaluations, dataset_date=dataset_date, source=source)
        for message in messages:
            payload = {"chat_id": chat_id, "text": message, "disable_web_page_preview": True}
            response = client.post(endpoint, json=payload, timeout=15)
            response.raise_for_status()
            body = response.json()
            if not body.get("ok"):
                return False, "Telegram API returned a non-success response."
        return True, f"Telegram TOP 50 report sent in {len(messages)} message(s)."
    except (requests.RequestException, ValueError):
        return False, "Telegram TOP 50 delivery failed; inspect workflow status without exposing secrets."


def send_test_message(
    bot_token: str | None = None,
    chat_id: str | None = None,
    session: requests.Session | None = None,
) -> tuple[bool, str]:
    """Send an explicit integration-test message when manually requested."""

    bot_token = bot_token or os.getenv("TELEGRAM_BOT_TOKEN", "")
    chat_id = chat_id or os.getenv("TELEGRAM_CHAT_ID", "")
    if not bot_token or not chat_id:
        return False, "Telegram credentials are not configured; test message not sent."
    endpoint = f"https://api.telegram.org/bot{bot_token}/sendMessage"
    payload = {
        "chat_id": chat_id,
        "text": "EXPIRED .COM DOMAIN HUNTER — Telegram integration test passed.",
        "disable_web_page_preview": True,
    }
    client = session or requests.Session()
    try:
        response = client.post(endpoint, json=payload, timeout=15)
        response.raise_for_status()
        body = response.json()
        if not body.get("ok"):
            return False, "Telegram API returned a non-success response for the test message."
        return True, "Telegram integration test message sent."
    except (requests.RequestException, ValueError):
        return False, "Telegram test delivery failed; inspect workflow status without exposing secrets."


def build_summary_for_test(evaluations: Iterable[Evaluation]) -> str:
    """Expose deterministic first-message formatting for unit tests."""

    return _summary_messages(evaluations)[0]
