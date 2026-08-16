"""Transparent domain scoring and conservative valuation heuristics."""

from __future__ import annotations

import math
import re
from dataclasses import dataclass

from config import BID_VALUE_RATIO, MAX_MAX_BID, MAX_PENALTY, MIN_MAX_BID, PENALTIES, classification
from src.data_source import DomainCandidate
from src.filters import FilterResult, domain_tokens
from src.history import HistorySignals

BUSINESS_TERMS = {
    "accounting",
    "agency",
    "analytics",
    "app",
    "billing",
    "brand",
    "business",
    "cloud",
    "commerce",
    "consulting",
    "crm",
    "data",
    "digital",
    "email",
    "finance",
    "growth",
    "invoice",
    "invoices",
    "legal",
    "logistics",
    "market",
    "marketing",
    "pay",
    "payment",
    "payments",
    "sales",
    "search",
    "secure",
    "shop",
    "software",
    "startup",
    "store",
    "tax",
    "tech",
    "trade",
    "web",
}

INDUSTRY_MAP = {
    "ai": "AI and machine-learning products",
    "analytics": "analytics and business intelligence",
    "app": "mobile and web applications",
    "accounting": "accounting and bookkeeping software",
    "billing": "billing and subscription platforms",
    "commerce": "ecommerce and retail technology",
    "crm": "customer relationship management",
    "data": "data and analytics services",
    "finance": "fintech and financial services",
    "invoice": "invoicing and accounts-receivable software",
    "marketing": "marketing and advertising services",
    "pay": "payments and checkout products",
    "payment": "payments and checkout products",
    "payments": "payments and checkout products",
    "sales": "sales enablement and revenue operations",
    "saas": "software-as-a-service products",
    "shop": "ecommerce and retail brands",
    "software": "software companies",
    "store": "online retail and marketplaces",
    "tech": "technology products and services",
    "web": "web design and development services",
}


@dataclass
class Evaluation:
    domain: str
    score: int
    classification: str
    suggested_max_bid: str
    estimated_resale_range: str
    brandability: int
    commercial_intent: int
    keyword_quality: int
    length_readability: int
    historical_quality: int
    backlink_quality: int
    age_history: int
    end_user_potential: int
    spam_risk: str
    potential_industries: str
    reason: str
    wayback_url: str
    source: str


def _label(candidate: DomainCandidate) -> str:
    return candidate.label.lower()


def _vowel_ratio(text: str) -> float:
    letters = [char for char in text if char.isalpha()]
    if not letters:
        return 0.0
    return sum(char in "aeiou" for char in letters) / len(letters)


def _has_commercial_signal(candidate: DomainCandidate, tokens: list[str]) -> bool:
    explicit = {token.lower() for token in re.findall(r"[a-z]+", candidate.keyword.lower())}
    contained = any(term in candidate.label.lower() for term in BUSINESS_TERMS)
    return bool(explicit & BUSINESS_TERMS or set(tokens) & BUSINESS_TERMS or contained or candidate.search_volume and candidate.search_volume > 100)


def _brandability(candidate: DomainCandidate, filters: FilterResult, tokens: list[str]) -> int:
    label = _label(candidate)
    score = 0
    if len(tokens) == 1:
        score += 8
    elif len(tokens) == 2:
        score += 7
    elif len(tokens) == 3:
        score += 3
    if 5 <= len(label) <= 12:
        score += 6
    elif len(label) <= 18:
        score += 3
    if _vowel_ratio(label) >= 0.28:
        score += 4
    if filters.numbers:
        score -= 4
    if filters.hyphens:
        score -= 4
    if filters.awkward_spelling:
        score -= 4
    return max(0, min(20, score))


def _commercial_intent(candidate: DomainCandidate, tokens: list[str]) -> int:
    keyword_tokens = set(re.findall(r"[a-z]+", candidate.keyword.lower()))
    all_tokens = set(tokens) | keyword_tokens
    score = 4 + min(10, 4 * len(all_tokens & BUSINESS_TERMS))
    if candidate.search_volume:
        if candidate.search_volume >= 10000:
            score += 6
        elif candidate.search_volume >= 1000:
            score += 4
        elif candidate.search_volume >= 100:
            score += 2
    return max(0, min(20, score))


def _keyword_quality(candidate: DomainCandidate, tokens: list[str]) -> int:
    keyword_tokens = set(re.findall(r"[a-z]+", candidate.keyword.lower()))
    overlap = len(set(tokens) & (keyword_tokens | BUSINESS_TERMS))
    contained_business = sum(term in candidate.label.lower() for term in BUSINESS_TERMS)
    score = min(10, (overlap + min(2, contained_business)) * 4)
    if len(tokens) in (1, 2):
        score += 3
    if candidate.search_volume and candidate.search_volume >= 1000:
        score += 2
    return max(0, min(15, score))


def _length_readability(candidate: DomainCandidate, filters: FilterResult) -> int:
    length = len(_label(candidate))
    if length <= 8:
        score = 10
    elif length <= 12:
        score = 8
    elif length <= 16:
        score = 6
    elif length <= 22:
        score = 4
    else:
        score = 1
    if filters.numbers:
        score -= 2
    if filters.hyphens:
        score -= 2
    if filters.awkward_spelling:
        score -= 2
    return max(0, min(10, score))


def _backlink_quality(candidate: DomainCandidate) -> int:
    ref_domains = max(0.0, candidate.ref_domains or 0.0)
    backlinks = max(0.0, candidate.backlinks or 0.0)
    score = min(7, int(math.log10(ref_domains + 1) * 3.2))
    score += min(3, int(math.log10(backlinks + 1)))
    return max(0, min(10, score))


def _age_history(candidate: DomainCandidate, history: HistorySignals) -> int:
    age = candidate.domain_age or 0
    score = min(3, int(age / 5))
    if history.first_year and history.first_year <= 2015:
        score += 2
    elif history.first_year:
        score += 1
    return max(0, min(5, score))


def _industries(candidate: DomainCandidate, tokens: list[str]) -> list[str]:
    text = set(tokens) | set(re.findall(r"[a-z]+", candidate.keyword.lower()))
    found: list[str] = []
    for token in tokens + list(text):
        industry = INDUSTRY_MAP.get(token)
        if industry and industry not in found:
            found.append(industry)
    for term, industry in INDUSTRY_MAP.items():
        if term in candidate.label.lower() and industry not in found:
            found.append(industry)
    if not found and _has_commercial_signal(candidate, tokens):
        found.append("general business services and brandable startups")
    return found[:4]


def _end_user_potential(candidate: DomainCandidate, industries: list[str], commercial: int) -> int:
    score = min(7, commercial // 3)
    if len(industries) >= 2:
        score += 2
    elif industries:
        score += 1
    if len(candidate.label) <= 12:
        score += 1
    return max(0, min(10, score))


def _valuation(score: int, end_user: int, ref_domains: float | None) -> tuple[str, str]:
    """Return a conservative range and max bid; not a guaranteed valuation."""

    if score >= 90:
        low, high = 1500.0, 5000.0
    elif score >= 80:
        low, high = 500.0, 2500.0
    elif score >= 65:
        low, high = 150.0, 900.0
    else:
        low, high = 0.0, 250.0
    multiplier = 1.0 + min(0.35, end_user / 30.0) + min(0.20, math.log10((ref_domains or 0) + 1) / 20)
    low *= multiplier
    high *= multiplier
    if low <= 0:
        resale = "$0-$250"
        bid = "$0"
    else:
        resale = f"${round(low / 50) * 50:,.0f}-${round(high / 50) * 50:,.0f}"
        max_bid = min(MAX_MAX_BID, max(MIN_MAX_BID, low * BID_VALUE_RATIO))
        bid = f"${round(max_bid):,.0f}"
    return resale, bid


def evaluate(candidate: DomainCandidate, filters: FilterResult, history: HistorySignals) -> Evaluation:
    tokens = domain_tokens(candidate)
    brandability = _brandability(candidate, filters, tokens)
    commercial = _commercial_intent(candidate, tokens)
    keyword_quality = _keyword_quality(candidate, tokens)
    length_readability = _length_readability(candidate, filters)
    historical = round(min(10, history.historical_quality))
    backlinks = _backlink_quality(candidate)
    age_history = _age_history(candidate, history)
    industries = _industries(candidate, tokens)
    end_user = _end_user_potential(candidate, industries, commercial)

    positive = brandability + commercial + keyword_quality + length_readability + historical + backlinks + age_history + end_user
    penalty = 0
    penalty += PENALTIES["numbers"] if filters.numbers else 0
    penalty += PENALTIES["hyphens"] if filters.hyphens else 0
    penalty += PENALTIES["awkward_spelling"] if filters.awkward_spelling else 0
    penalty += PENALTIES["suspicious_history"] if history.suspicious_changes else 0
    penalty += PENALTIES["spam"] if filters.spam_signal or history.spam_like else 0
    penalty += PENALTIES["trademark_risk"] if filters.trademark_risk else 0
    if not _has_commercial_signal(candidate, tokens):
        penalty += PENALTIES["weak_commercial_potential"]
    penalty = min(MAX_PENALTY, penalty)
    score = max(0, min(100, round(positive - penalty)))
    class_name = classification(score)
    resale, bid = _valuation(score, end_user, candidate.ref_domains)

    risk_parts = []
    if filters.spam_signal or history.spam_like:
        risk_parts.append("high")
    elif filters.trademark_risk or history.suspicious_changes:
        risk_parts.append("medium")
    else:
        risk_parts.append("low")
    reasons = []
    if industries:
        reasons.append(f"Real end-user fit across {', '.join(industries[:2])}")
    if len(tokens) <= 2 and length_readability >= 6:
        reasons.append("short, readable naming structure")
    if history.snapshots:
        reasons.append(history.previous_use)
    if filters.trademark_risk:
        reasons.append("possible trademark risk requires manual review")
    reasons.append("AI estimate — manual verification required")

    return Evaluation(
        domain=candidate.normalized_domain,
        score=score,
        classification=class_name,
        suggested_max_bid=bid,
        estimated_resale_range=resale,
        brandability=brandability,
        commercial_intent=commercial,
        keyword_quality=keyword_quality,
        length_readability=length_readability,
        historical_quality=historical,
        backlink_quality=backlinks,
        age_history=age_history,
        end_user_potential=end_user,
        spam_risk=risk_parts[0],
        potential_industries="; ".join(industries) or "No clear industry signal",
        reason="; ".join(reasons),
        wayback_url=history.wayback_url,
        source=candidate.source,
    )
