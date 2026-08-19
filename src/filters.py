"""Deterministic quality and safety filters for candidate domains."""

from __future__ import annotations

import re
from dataclasses import dataclass

from .data_source import DomainCandidate

# These are deliberately conservative risk signals, not a complete legal or
# content classifier. The README explains that manual verification is required.
PROHIBITED_TERMS = {
    "adult",
    "xxx",
    "porn",
    "sexcam",
    "casino",
    "poker",
    "betting",
    "sportsbook",
    "gambling",
    "drug",
    "pharma",
    "opioid",
    "cocaine",
    "malware",
    "ransomware",
    "hack",
    "hacker",
    "exploit",
    "botnet",
    "weapon",
    "fraud",
    "scam",
}

TRADEMARK_RISK_TERMS = {
    "google",
    "apple",
    "amazon",
    "microsoft",
    "facebook",
    "instagram",
    "tiktok",
    "paypal",
    "stripe",
    "adobe",
    "oracle",
    "tesla",
    "nike",
    "coca-cola",
    "visma",
    "webull",
    "whatsapp",
}

ACTIVE_STATUS_TERMS = {"active", "registered", "live", "reserved"}
EXCLUDED_STATUS_MARKERS = {
    "pending",
    "auction",
    "backorder",
    "expiring",
    "pre-release",
    "prerelease",
    "buy now",
    "aftermarket",
    "bidding",
}
TOKEN_RE = re.compile(r"[a-z]+|[0-9]+", re.IGNORECASE)
VOWELS = set("aeiou")


@dataclass
class FilterResult:
    accepted: bool
    prohibited: bool = False
    spam_signal: bool = False
    trademark_risk: bool = False
    numbers: bool = False
    hyphens: bool = False
    awkward_spelling: bool = False
    weak_commercial_potential: bool = False
    reasons: list[str] | None = None


def domain_tokens(candidate: DomainCandidate) -> list[str]:
    return TOKEN_RE.findall(candidate.label.lower())


def _contains_term(tokens: list[str], terms: set[str]) -> str | None:
    joined = "".join(tokens)
    dotted = "-".join(tokens)
    for term in terms:
        if term in tokens or term in joined or term in dotted:
            return term
    return None


def inspect_candidate(candidate: DomainCandidate) -> FilterResult:
    label = candidate.label
    tokens = domain_tokens(candidate)
    reasons: list[str] = []
    prohibited_term = _contains_term(tokens, PROHIBITED_TERMS)
    trademark_term = _contains_term(tokens, TRADEMARK_RISK_TERMS)
    has_numbers = bool(re.search(r"\d", label))
    has_hyphens = "-" in label
    repeated_chars = bool(re.search(r"(.)\1\1", label))
    too_many_digits = sum(char.isdigit() for char in label) >= 2
    excessive_separators = label.count("-") >= 2
    long_label = len(label) > 24
    consonant_run = bool(re.search(r"[^aeiou-]{6,}", label))
    token_count = len(tokens)
    weak_commercial = token_count == 0 or len(label) > 30

    if prohibited_term:
        reasons.append(f"prohibited/high-risk term: {prohibited_term}")
    if trademark_term:
        reasons.append(f"possible trademark risk signal: {trademark_term}")
    if has_numbers:
        reasons.append("contains numbers")
    if has_hyphens:
        reasons.append("contains hyphen")
    if repeated_chars:
        reasons.append("repeated-character spam signal")
    if too_many_digits:
        reasons.append("excessive numbers")
    if excessive_separators:
        reasons.append("multiple hyphens")
    if long_label:
        reasons.append("long label")
    if consonant_run:
        reasons.append("difficult consonant cluster")

    spam_signal = repeated_chars or too_many_digits or excessive_separators or label.startswith("xn--")
    awkward_spelling = consonant_run or repeated_chars or (len(label) >= 14 and not any(v in label for v in VOWELS))
    accepted = not prohibited_term and candidate.normalized_domain.endswith(".com")
    status = candidate.status.strip().lower()
    if status in ACTIVE_STATUS_TERMS or any(marker in status for marker in EXCLUDED_STATUS_MARKERS):
        accepted = False
        reasons.append(f"status is not hand-registerable expired/dropped inventory: {candidate.status}")

    return FilterResult(
        accepted=accepted,
        prohibited=bool(prohibited_term),
        spam_signal=spam_signal,
        trademark_risk=bool(trademark_term),
        numbers=has_numbers,
        hyphens=has_hyphens,
        awkward_spelling=awkward_spelling,
        weak_commercial_potential=weak_commercial,
        reasons=reasons,
    )
