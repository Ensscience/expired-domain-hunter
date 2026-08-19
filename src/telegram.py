"""Telegram delivery for AVAILABLE-only expired .COM reports."""

from __future__ import annotations

import os
from typing import Iterable

import requests

from config import FINAL_SCORE_THRESHOLD
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


def _eligible(evaluations: Iterable[Evaluation]) -> list[Evaluation]:
    return [item for item in evaluations if str(getattr(item, "registration_status", "UNKNOWN")).upper() == "AVAILABLE" and float(item.score) >= FINAL_SCORE_THRESHOLD]


def _entry(index: int, item: Evaluation) -> str:
    return "\n".join(
        [
            f"{index}. {item.domain}",
            f"Score: {item.score:.1f}/10",
            f"Classification: {item.classification}",
            f"Availability: {_status_label(getattr(item, 'registration_status', 'UNKNOWN'))}",
            f"Source: {getattr(item, 'sources', '') or getattr(item, 'source', 'unknown')}",
            f"Why it is valuable: {item.reason}",
            f"Main strength: {getattr(item, 'main_strength', '')}",
            f"Main weakness: {getattr(item, 'main_weakness', '')}",
            f"Estimated resale: {getattr(item, 'estimated_resale_range', 'manual review required')}",
            *( [f"Trademark risk: {item.trademark_risk_flag}"] if getattr(item, 'trademark_risk_flag', '') else [] ),
        ]
    )


def _summary_messages(
    evaluations: Iterable[Evaluation],
    dataset_date: str = "",
    source: str = "",
    dataset_id: str = "",
    *,
    counts: dict[str, int] | None = None,
    quality_candidates: int | None = None,
    rdap_checked: int | None = None,
) -> list[str]:
    ordered = _eligible(evaluations)[:50]
    date_value = dataset_date or "unknown"
    source_value = source or "WhoisFreaks public expired feed"
    prefix = "🔥 TOP AVAILABLE EXPIRED .COM\n\n"
    prefix += f"Source: {source_value}\n"
    prefix += f"Collected: {date_value}\n"
    if dataset_id:
        prefix += f"Dataset: {dataset_id}\n"
    prefix += "Score threshold: AVAILABLE + score >= 7.0/10\n"
    prefix += "RDAP is point-in-time; verify at a registrar immediately before registration.\n\n"
    count_data = counts or {}
    footer = (
        "\n\n---\n"
        + f"Candidates collected: {count_data.get('RAW_ROWS', 0)}\n"
        + f"Valid .COM: {count_data.get('VALID_COM', 0)}\n"
        + f"Rejected: {count_data.get('REJECTED', 0)}\n"
        + f"Duplicates: {count_data.get('DUPLICATES', 0)}\n"
        + f"Quality candidates: {quality_candidates if quality_candidates is not None else 0}\n"
        + f"RDAP checked: {rdap_checked if rdap_checked is not None else 0}\n"
        + f"AVAILABLE: {count_data.get('AVAILABLE', 0)}\n"
        + f"REGISTERED: {count_data.get('REGISTERED', 0)}\n"
        + f"PENDING: {count_data.get('PENDING', 0)}\n"
        + f"UNKNOWN: {count_data.get('UNKNOWN', 0)}\n"
        + f"Final candidates: {len(ordered)}"
    )
    if not ordered:
        return [prefix + "🔎 No high-quality available expired .COM domains were verified in this dataset." + footer]

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
    if messages:
        if len(messages[-1]) + len(footer) <= TELEGRAM_MESSAGE_LIMIT:
            messages[-1] += footer
        else:
            messages.append(footer.lstrip())
    return messages


def send_daily_summary(
    evaluations: Iterable[Evaluation],
    bot_token: str | None = None,
    chat_id: str | None = None,
    session: requests.Session | None = None,
    *,
    dataset_date: str = "",
    source: str = "",
    dataset_id: str = "",
    counts: dict[str, int] | None = None,
    quality_candidates: int | None = None,
    rdap_checked: int | None = None,
) -> tuple[bool, str]:
    """Send only AVAILABLE domains scoring at least 7.0/10, or a zero-result message."""

    bot_token = bot_token or os.getenv("TELEGRAM_BOT_TOKEN", "")
    chat_id = chat_id or os.getenv("TELEGRAM_CHAT_ID", "")
    if not bot_token or not chat_id:
        return False, "Telegram credentials are not configured; AVAILABLE-only report not sent."

    endpoint = f"https://api.telegram.org/bot{bot_token}/sendMessage"
    client = session or requests.Session()
    try:
        messages = _summary_messages(evaluations, dataset_date=dataset_date, source=source, dataset_id=dataset_id, counts=counts, quality_candidates=quality_candidates, rdap_checked=rdap_checked)
        for message in messages:
            payload = {"chat_id": chat_id, "text": message, "disable_web_page_preview": True}
            response = client.post(endpoint, json=payload, timeout=15)
            response.raise_for_status()
            body = response.json()
            if not body.get("ok"):
                return False, "Telegram API returned a non-success response."
        return True, f"Telegram AVAILABLE-only report sent in {len(messages)} message(s)."
    except (requests.RequestException, ValueError):
        return False, "Telegram AVAILABLE-only delivery failed; inspect workflow status without exposing secrets."


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
    payload = {"chat_id": chat_id, "text": "EXPIRED .COM DOMAIN HUNTER — Telegram integration test passed.", "disable_web_page_preview": True}
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
