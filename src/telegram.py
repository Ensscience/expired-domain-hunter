"""Telegram summary delivery for qualifying domains."""

from __future__ import annotations

import os
from typing import Iterable

import requests

from config import TELEGRAM_MAX_ITEMS
from src.scoring import Evaluation


def _summary_message(evaluations: Iterable[Evaluation]) -> str:
    qualifying = [item for item in evaluations if item.score >= 80][:TELEGRAM_MAX_ITEMS]
    lines = ["EXPIRED .COM DOMAIN HUNTER", "", "Top opportunities today:"]
    for index, item in enumerate(qualifying, start=1):
        lines.extend(
            [
                "",
                f"{index}. {item.domain}",
                f"   Score: {item.score}/100",
                f"   Max bid: {item.suggested_max_bid}",
                f"   Estimated resale: {item.estimated_resale_range}",
                f"   Why: {item.reason}",
            ]
        )
    lines.extend(["", "AI estimates only — manual verification required."])
    return "\n".join(lines)


def send_daily_summary(
    evaluations: Iterable[Evaluation],
    bot_token: str | None = None,
    chat_id: str | None = None,
    session: requests.Session | None = None,
) -> tuple[bool, str]:
    """Send one summary only when at least one domain scores 80+.

    Missing credentials are a normal local-development state. The return
    message is safe to print and never contains token or chat-id values.
    """

    qualifying = [item for item in evaluations if item.score >= 80]
    if not qualifying:
        return False, "No qualifying domains; Telegram summary not sent."
    bot_token = bot_token or os.getenv("TELEGRAM_BOT_TOKEN", "")
    chat_id = chat_id or os.getenv("TELEGRAM_CHAT_ID", "")
    if not bot_token or not chat_id:
        return False, "Telegram credentials are not configured; summary not sent."

    endpoint = f"https://api.telegram.org/bot{bot_token}/sendMessage"
    payload = {"chat_id": chat_id, "text": _summary_message(qualifying), "disable_web_page_preview": True}
    client = session or requests.Session()
    try:
        response = client.post(endpoint, json=payload, timeout=15)
        response.raise_for_status()
        body = response.json()
        if not body.get("ok"):
            return False, "Telegram API returned a non-success response."
        return True, "Telegram daily summary sent."
    except (requests.RequestException, ValueError):
        return False, "Telegram delivery failed; inspect the workflow status without exposing secrets."


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
        return False, "Telegram test delivery failed; inspect the workflow status without exposing secrets."


def build_summary_for_test(evaluations: Iterable[Evaluation]) -> str:
    """Expose deterministic message formatting for unit tests."""

    return _summary_message(evaluations)
