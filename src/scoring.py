"""Transparent, conservative domain-investor scoring heuristics."""

from __future__ import annotations

import math
import re
from dataclasses import dataclass
from functools import lru_cache

from config import BID_VALUE_RATIO, MAX_MAX_BID, MAX_PENALTY, MIN_MAX_BID, PENALTIES, classification
from src.data_source import DomainCandidate
from src.filters import FilterResult, domain_tokens
from src.history import HistorySignals

# This is intentionally a conservative, dependency-free investor lexicon rather
# than a claim of complete dictionary coverage. Unknown tokens never receive the
# same word-quality credit as a recognized English word.
ENGLISH_WORDS = set("""
account accounting active add agency air alert alpha amount annual app area asset assist audit auto balance bank base beam beauty benefit best better bill billing blue boost brand bridge bright build business buyer buy capital care cash center central change charge check city clean client cloud code common company connect contact content contract control core cost craft credit crm data date deal deep design detail digital direct discover doctor domain draft drive early easy ecommerce edge email energy engine enjoy event expert fast feature field file finance find firm fit flow focus food form fresh future gain game global go good goods grade green group growth guide health help hero high home host idea image impact improve income index industry insight insurance invest invoice invoices job key kind lab lead ledger legal level light link local logic market marketing master media medical member message mobile model modern money monitor move name natural network new niche note number office online open option order organic owner partner pay payment payments people personal plan platform point policy premium price product profit program project proof property protect quality quick real read realty reason record remote report research resource retail revenue rich right risk safe sales save scale secure service share shop signal simple smart social software solution source space speed stable store strategy stream strong studio success support system target task team tech technology time tool trade training travel trust user value video view vision web website work world worth write yield zone
""".split())

GENERIC_TERMS = {
    "tech", "web", "app", "shop", "store", "pay", "online", "digital", "media",
    "solutions", "services", "hub", "pro", "labs", "lab", "world", "zone", "site",
    "cloud", "market", "group", "works", "now", "go", "up", "icon", "otc",
}
STRONG_COMMERCIAL_TERMS = {
    "account", "accounting", "analytics", "bank", "billing", "business", "capital", "cash",
    "commerce", "commerce", "credit", "crm", "data", "email", "finance", "growth", "health", "pay",
    "income", "insurance", "invoice", "invoices", "ledger", "legal", "logistics", "marketing",
    "money", "payment", "payments", "property", "retail", "revenue", "sales", "secure", "software",
    "tax", "trade", "technology", "wallet", "wealth",
}
KNOWN_ABBREVIATIONS = {"ai", "crm", "otc", "saas", "seo", "api", "io", "hq"}

# Pairs that are conventional enough to receive natural-order credit. The list
# is deliberately selective; two commercial words are not automatically natural.
NATURAL_PAIRS = {
    ("cloud", "pay"), ("cloud", "ledger"), ("cloud", "data"), ("fast", "money"),
    ("smart", "invoice"), ("smart", "invoices"), ("smart", "billing"), ("secure", "pay"),
    ("secure", "data"), ("digital", "market"), ("data", "ledger"), ("data", "flow"),
    ("sales", "force"), ("health", "care"), ("property", "market"), ("credit", "union"),
    ("growth", "lab"), ("brand", "studio"), ("email", "marketing"), ("payment", "flow"),
    ("payments", "flow"), ("invoice", "flow"), ("finance", "hub"), ("market", "place"),
    ("home", "care"), ("green", "energy"), ("bright", "future"), ("quick", "sale"),
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
    status: str = ""
    registration_status: str = "UNKNOWN"
    source_count: int = 1
    sources: str = ""
    score_stage: str = "FINAL"
    main_strength: str = ""
    main_weakness: str = ""
    trademark_risk_flag: str = ""


def _label(candidate: DomainCandidate) -> str:
    return candidate.label.lower()


def _vowel_ratio(text: str) -> float:
    letters = [char for char in text if char.isalpha()]
    if not letters:
        return 0.0
    return sum(char in "aeiou" for char in letters) / len(letters)


def _known_word(token: str) -> bool:
    return token in ENGLISH_WORDS or token in KNOWN_ABBREVIATIONS


def _best_segmentation(text: str) -> list[str] | None:
    text = text.lower()

    @lru_cache(maxsize=None)
    def solve(position: int) -> tuple[str, ...] | None:
        if position == len(text):
            return ()
        best: tuple[str, ...] | None = None
        for word in sorted(ENGLISH_WORDS | KNOWN_ABBREVIATIONS, key=len, reverse=True):
            if len(word) < 3 and word not in KNOWN_ABBREVIATIONS:
                continue
            if text.startswith(word, position):
                remainder = solve(position + len(word))
                if remainder is not None:
                    candidate = (word,) + remainder
                    if best is None or len(candidate) < len(best):
                        best = candidate
        return best

    result = solve(0)
    return list(result) if result else None


def _semantic_tokens(candidate: DomainCandidate) -> list[str]:
    raw = domain_tokens(candidate)
    if len(raw) > 1:
        return raw
    if not raw:
        return []
    text = raw[0]
    full = _best_segmentation(text)
    if full:
        return full

    # Partial segmentation gives a conservative known-word plus unknown-word
    # representation for names such as data+kudi and ukran+tech.
    words = sorted(ENGLISH_WORDS | KNOWN_ABBREVIATIONS, key=len, reverse=True)
    for word in words:
        if len(word) >= 3 and text.startswith(word) and len(text) - len(word) >= 3:
            return [word, text[len(word):]]
        if len(word) >= 3 and text.endswith(word) and len(text) - len(word) >= 3:
            return [text[:-len(word)], word]
    return [text]


def _real_word_ratio(tokens: list[str]) -> float:
    if not tokens:
        return 0.0
    return sum(_known_word(token) for token in tokens) / len(tokens)


def _generic_tokens(tokens: list[str]) -> list[str]:
    return [token for token in tokens if token in GENERIC_TERMS]


def _strong_terms(tokens: list[str], candidate: DomainCandidate) -> set[str]:
    keyword_tokens = set(re.findall(r"[a-z]+", candidate.keyword.lower()))
    return (set(tokens) | keyword_tokens) & STRONG_COMMERCIAL_TERMS


def _is_natural_combination(tokens: list[str]) -> bool:
    if len(tokens) == 1:
        return _known_word(tokens[0])
    if len(tokens) == 2:
        return tuple(tokens) in NATURAL_PAIRS
    return False


def _trademark_pattern(candidate: DomainCandidate, tokens: list[str], filters: FilterResult) -> bool:
    if filters.trademark_risk:
        return True
    if not tokens:
        return False
    unknown = [token for token in tokens if not _known_word(token)]
    generic = _generic_tokens(tokens)
    # Unknown tokens alone are a quality issue, not a trademark conclusion.
    # Explicit company-name signals and known brand-like abbreviations remain
    # review flags without claiming infringement.
    if unknown and tokens[-1] in KNOWN_ABBREVIATIONS:
        return True
    return False


def _has_commercial_signal(candidate: DomainCandidate, tokens: list[str]) -> bool:
    return bool(_strong_terms(tokens, candidate))


def _brandability(candidate: DomainCandidate, filters: FilterResult, tokens: list[str], natural: bool, real_ratio: float, generic_count: int) -> int:
    label = _label(candidate)
    score = 0
    if len(tokens) == 1 and real_ratio == 1 and len(label) <= 10:
        score += 14
    elif len(tokens) == 2 and real_ratio == 1 and natural:
        score += 12
    elif len(tokens) == 2 and real_ratio == 1:
        score += 6
    elif real_ratio >= 0.5:
        score += 3
    if 1 <= len(label) <= 8:
        score += 5
    elif len(label) <= 10:
        score += 4
    elif len(label) <= 12:
        score += 2
    if _vowel_ratio(label) >= 0.30 and not filters.awkward_spelling:
        score += 2
    if natural:
        score += 2
    score -= generic_count * (1 if natural else 3)
    score -= sum(not _known_word(token) for token in tokens) * 4
    if len(tokens) >= 3:
        score -= 5
    if filters.numbers:
        score -= 5
    if filters.hyphens:
        score -= 4
    if filters.awkward_spelling:
        score -= 4
    return max(0, min(20, score))


def _commercial_intent(candidate: DomainCandidate, tokens: list[str], natural: bool, real_ratio: float, generic_count: int) -> int:
    strong = _strong_terms(tokens, candidate)
    score = 0
    if strong and real_ratio >= 0.5:
        score = 6 + min(10, 4 * len(strong))
        if natural:
            score += 2
    elif strong:
        score = 4
    if generic_count and not strong:
        score = min(score, 2)
    if candidate.search_volume and real_ratio >= 0.5 and strong:
        if candidate.search_volume >= 10000:
            score += 4
        elif candidate.search_volume >= 1000:
            score += 3
        elif candidate.search_volume >= 100:
            score += 1
    return max(0, min(20, score))


def _keyword_quality(candidate: DomainCandidate, tokens: list[str], natural: bool, real_ratio: float, generic_count: int) -> int:
    strong = _strong_terms(tokens, candidate)
    known = sum(_known_word(token) for token in tokens)
    score = min(8, known * 4)
    if real_ratio == 1 and len(tokens) <= 2:
        score += 3
    if natural:
        score += 2
    if strong:
        score += 2
    if generic_count >= 2 and not strong:
        score = min(score, 2)
    if candidate.search_volume and candidate.search_volume >= 1000 and real_ratio >= 0.5:
        score += 1
    return max(0, min(15, score))


def _length_readability(candidate: DomainCandidate, filters: FilterResult, tokens: list[str], natural: bool) -> int:
    length = len(_label(candidate))
    if length <= 8:
        score = 10
    elif length <= 10:
        score = 8
    elif length <= 12:
        score = 6
    elif length <= 16:
        score = 4
    elif length <= 22:
        score = 2
    else:
        score = 0
    if len(tokens) >= 3:
        score -= 3
    if len(tokens) >= 3 and not natural:
        score -= 2
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
    industry_map = {
        "accounting": "accounting software",
        "analytics": "analytics software",
        "billing": "billing software",
        "commerce": "ecommerce technology",
        "credit": "credit and lending",
        "data": "data products",
        "email": "email software",
        "finance": "financial services",
        "health": "health services",
        "insurance": "insurance services",
        "invoice": "invoicing software",
        "invoices": "invoicing software",
        "ledger": "accounting and finance software",
        "legal": "legal services",
        "logistics": "logistics software",
        "marketing": "marketing software",
        "money": "payments and finance",
        "payment": "payments software",
        "payments": "payments software",
        "property": "property services",
        "retail": "retail technology",
        "revenue": "revenue software",
        "sales": "sales software",
        "secure": "security products",
        "software": "software products",
        "tax": "tax software",
        "trade": "trade services",
        "technology": "technology products",
        "wallet": "payments and finance",
        "wealth": "wealth management",
    }
    found: list[str] = []
    for token in tokens + re.findall(r"[a-z]+", candidate.keyword.lower()):
        industry = industry_map.get(token)
        if industry and industry not in found:
            found.append(industry)
    return found[:3]


def _end_user_potential(candidate: DomainCandidate, industries: list[str], natural: bool, real_ratio: float, strong: set[str], generic_count: int, trademark_risk: bool) -> int:
    if trademark_risk or real_ratio < 1 or generic_count >= 2:
        return 0
    score = 0
    if len(industries) == 1:
        score += 3
    elif len(industries) >= 2:
        score += 4
    if natural:
        score += 3
    if strong:
        score += 2
    if len(candidate.label) <= 10:
        score += 1
    return max(0, min(10, score))


def _valuation(score: int, end_user: int, ref_domains: float | None) -> tuple[str, str]:
    """Return a conservative range and max bid; not a guaranteed valuation."""

    if score >= 90:
        low, high = 1500.0, 5000.0
    elif score >= 80:
        low, high = 500.0, 2500.0
    elif score >= 70:
        low, high = 250.0, 1200.0
    elif score >= 60:
        low, high = 100.0, 600.0
    elif score >= 50:
        low, high = 25.0, 300.0
    else:
        low, high = 0.0, 150.0
    multiplier = 1.0 + min(0.25, end_user / 40.0) + min(0.15, math.log10((ref_domains or 0) + 1) / 25)
    low *= multiplier
    high *= multiplier
    if low <= 0:
        resale = "$0-$150"
        bid = "$0"
    else:
        resale = f"${round(low / 25) * 25:,.0f}-${round(high / 25) * 25:,.0f}"
        max_bid = min(MAX_MAX_BID, max(MIN_MAX_BID, low * BID_VALUE_RATIO))
        bid = f"${round(max_bid):,.0f}"
    return resale, bid


def evaluate(candidate: DomainCandidate, filters: FilterResult, history: HistorySignals) -> Evaluation:
    tokens = _semantic_tokens(candidate)
    real_ratio = _real_word_ratio(tokens)
    generic = _generic_tokens(tokens)
    natural = _is_natural_combination(tokens)
    strong = _strong_terms(tokens, candidate)
    trademark_risk = _trademark_pattern(candidate, tokens, filters)
    brandability = _brandability(candidate, filters, tokens, natural, real_ratio, len(generic))
    commercial = _commercial_intent(candidate, tokens, natural, real_ratio, len(generic))
    keyword_quality = _keyword_quality(candidate, tokens, natural, real_ratio, len(generic))
    length_readability = _length_readability(candidate, filters, tokens, natural)
    historical = round(min(10, history.historical_quality))
    backlinks = _backlink_quality(candidate)
    age_history = _age_history(candidate, history)
    industries = _industries(candidate, tokens)
    end_user = _end_user_potential(candidate, industries, natural, real_ratio, strong, len(generic), trademark_risk)

    positive = brandability + commercial + keyword_quality + length_readability + historical + backlinks + age_history + end_user
    penalty = 0
    penalty += PENALTIES["numbers"] if filters.numbers else 0
    penalty += PENALTIES["hyphens"] if filters.hyphens else 0
    penalty += PENALTIES["awkward_spelling"] if filters.awkward_spelling else 0
    penalty += PENALTIES["suspicious_history"] if history.suspicious_changes else 0
    penalty += PENALTIES["spam"] if filters.spam_signal or history.spam_like else 0
    penalty += PENALTIES["trademark_risk"] if trademark_risk else 0
    penalty += PENALTIES["weak_commercial_potential"] if not strong else 0
    penalty += PENALTIES["generic_suffix"] if generic and not natural else 0
    penalty += PENALTIES["keyword_stuffing"] if (len(generic) >= 2 and not natural) or len(tokens) >= 3 else 0
    penalty += PENALTIES["invented_string"] if real_ratio < 1 else 0
    penalty += PENALTIES["long_three_word"] if len(tokens) >= 3 else 0
    penalty = min(MAX_PENALTY, penalty)
    score = max(0, min(100, round(positive - penalty)))
    class_name = classification(score)
    resale, bid = _valuation(score, end_user, candidate.ref_domains)

    if trademark_risk:
        risk = "high"
    elif filters.spam_signal or history.spam_like:
        risk = "high"
    elif filters.awkward_spelling or real_ratio < 1:
        risk = "medium"
    else:
        risk = "low"

    if len(tokens) == 1 and real_ratio == 1:
        main_strength = "Short recognized English word with clean recall and single-name brandability."
    elif len(tokens) == 2 and real_ratio == 1 and natural:
        main_strength = f"Natural two-word phrase: {' '.join(tokens)}."
    elif strong and natural and real_ratio == 1:
        main_strength = f"Clear commercial phrase for {', '.join(industries[:2]) or 'a specific business market'}."
    elif real_ratio == 1:
        main_strength = "Recognized English words provide some readability, but the phrase is not strongly established."
    else:
        main_strength = "No strong investor-grade naming strength was established from the available signals."

    weaknesses: list[str] = []
    if real_ratio < 1:
        weaknesses.append("contains an unrecognized or invented token")
    if len(tokens) >= 3:
        weaknesses.append("three-or-more-word structure")
    if generic and not natural:
        weaknesses.append("generic modifier or keyword-stuffed combination")
    if len(tokens) >= 2 and not natural:
        weaknesses.append("word combination is not a clearly natural phrase")
    if not strong:
        weaknesses.append("weak specific commercial intent")
    if trademark_risk:
        weaknesses.append("possible trademark/company-name pattern")
    if filters.numbers or filters.hyphens or filters.awkward_spelling:
        weaknesses.append("spelling or formatting friction")
    main_weakness = "; ".join(weaknesses[:2]) or "No major structural weakness detected, subject to manual review."

    reasons: list[str] = [main_strength, f"Main weakness: {main_weakness}"]
    if industries and natural and strong:
        reasons.append(f"Specific market signal: {', '.join(industries[:2])}.")
    if history.snapshots:
        reasons.append(history.previous_use)
    if trademark_risk:
        reasons.append("Trademark risk — manual review required.")
    reasons.append("Heuristic resale estimate — manual verification required")

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
        spam_risk=risk,
        potential_industries="; ".join(industries) or "No specific end-user industry established",
        reason="; ".join(reasons),
        wayback_url=history.wayback_url,
        source=candidate.source,
        main_strength=main_strength,
        main_weakness=main_weakness,
        trademark_risk_flag="Trademark risk — manual review required." if trademark_risk else "",
    )
