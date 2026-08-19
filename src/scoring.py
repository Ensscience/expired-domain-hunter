"""Strict investor-oriented .COM scoring on a 0–10 scale."""

from __future__ import annotations

import math
import re
from dataclasses import dataclass
from functools import lru_cache

import wordninja
from wordfreq import zipf_frequency

from config import BID_VALUE_RATIO, MAX_MAX_BID, MAX_PENALTY, MIN_MAX_BID, PENALTIES, classification
from src.data_source import DomainCandidate
from src.filters import FilterResult, domain_tokens
from src.history import HistorySignals

ENGLISH_WORDS = set("""
account accounting active add agency air alert alpha amount annual app area asset assist audit auto balance bank base beam beauty benefit best better bill billing blue boost brand bridge bright build business buyer buy capital care cash center central change charge city clean client cloud code common company connect contact content contract control core cost craft credit crm data date deal deep design detail digital direct discover doctor domain draft drive early easy ecommerce edge email energy engine enjoy event expert fast feature field file finance find firm fit flow focus food form fresh future gain game global go good goods grade green group growth guide health help hero home host idea image impact improve income index industry insight insurance invest invoice invoices job key kind lab lead ledger legal level light link local logic market marketing master media medical member message mobile model modern money monitor move name natural network new niche note number office online open option order organic owner partner pay payment payments people personal plan platform point policy premium price product profit program project proof property protect quality quick real read realty reason record remote report research resource retail revenue rich right risk safe sales save scale secure service share shop signal simple smart social software solution source space speed stable store strategy stream strong studio success support system target task tech technology time tool trade training travel trust user value video view vision web website work world worth write yield zone
""".split())

GENERIC_TERMS = {
    "tech", "web", "app", "shop", "store", "pay", "online", "digital", "media",
    "solutions", "services", "hub", "pro", "labs", "lab", "world", "zone", "site",
    "cloud", "market", "group", "works", "now", "go", "up", "icon", "otc",
}
STRONG_COMMERCIAL_TERMS = {
    "account", "accounting", "analytics", "bank", "billing", "business", "capital", "cash",
    "commerce", "credit", "crm", "data", "finance", "growth", "health", "income", "insurance",
    "invoice", "invoices", "ledger", "legal", "logistics", "marketing", "money", "payment",
    "payments", "property", "retail", "revenue", "sales", "secure", "software", "security",
    "steel", "balustrade", "gates", "tax", "trade", "technology", "wallet", "wealth",
}
KNOWN_ABBREVIATIONS = {"ai", "crm", "otc", "saas", "seo", "api", "io", "hq"}
FUNCTION_WORDS = {"a", "an", "the", "of", "to", "in", "on", "for", "by", "my", "go", "up"}
COMMON_WORD_ZIPF = 3.0
UNCOMMON_WORD_ZIPF = 2.0
CATEGORY_HEADS = {
    "cloud", "health", "finance", "market", "trade", "data", "capital", "home", "legal",
    "security", "steel", "property", "credit", "growth", "sales", "payment", "payments",
    "invoice", "invoices", "money", "cash", "bank", "software", "email", "commerce",
    "insurance", "business", "accounting", "care", "energy", "ledger",
}
ADJECTIVE_HEADS = {"fast", "smart", "secure", "bright", "green", "quick", "strong", "simple", "fresh", "clear", "safe", "easy", "active", "best", "better", "direct", "global", "modern", "premium", "prime"}
VERB_HEADS = {"buy", "build", "grow", "find", "save", "protect", "track", "manage", "check", "make", "run", "share", "scale", "invest"}
PRODUCT_NOUNS = {
    "balustrade", "ledger", "invoice", "invoices", "billing", "care", "gates", "prices", "center",
    "market", "sales", "stories", "window", "windows", "service", "services", "studio", "lab",
    "flow", "future", "energy", "capital", "property", "money", "bank", "software", "security",
    "insurance", "accounting", "analytics", "commerce", "technology", "tax",
}
NATURAL_PAIRS = {
    ("cloud", "pay"), ("cloud", "ledger"), ("cloud", "data"), ("fast", "money"),
    ("smart", "invoice"), ("smart", "invoices"), ("smart", "billing"), ("secure", "pay"),
    ("secure", "data"), ("digital", "market"), ("data", "ledger"), ("data", "flow"),
    ("health", "care"), ("property", "market"), ("credit", "union"), ("growth", "lab"),
    ("brand", "studio"), ("email", "marketing"), ("payment", "flow"), ("payments", "flow"),
    ("invoice", "flow"), ("finance", "hub"), ("market", "place"), ("home", "care"),
    ("green", "energy"), ("bright", "future"), ("quick", "sale"), ("trade", "stories"),
    ("steel", "balustrade"),
}
PERSONAL_NAME_TOKENS = {"aaron", "antoine", "david", "james", "john", "michael", "mortimer", "weldon", "veda", "mehdi", "asaf", "mater"}


@dataclass
class Evaluation:
    domain: str
    score: float
    classification: str
    suggested_max_bid: str
    estimated_resale_range: str
    brandability: float
    commercial_intent: float
    keyword_quality: float
    length_readability: float
    historical_quality: float
    backlink_quality: float
    age_history: float
    end_user_potential: float
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
    natural_language_quality: float = 0.0
    shortness_memorability: float = 0.0
    resale_potential: float = 0.0
    broad_clean_market_appeal: float = 0.0
    penalty_total: float = 0.0
    score_scale: str = "0-10"


def _label(candidate: DomainCandidate) -> str:
    return candidate.label.lower()


def _vowel_ratio(text: str) -> float:
    letters = [char for char in text if char.isalpha()]
    return sum(char in "aeiou" for char in letters) / len(letters) if letters else 0.0


def _word_frequency(token: str) -> float:
    if len(token) < 3 and token not in FUNCTION_WORDS and token not in KNOWN_ABBREVIATIONS:
        return 0.0
    return zipf_frequency(token, "en")


def _known_word(token: str) -> bool:
    if token in ENGLISH_WORDS or token in KNOWN_ABBREVIATIONS or token in FUNCTION_WORDS:
        return True
    minimum = 2.5 if len(token) <= 3 else UNCOMMON_WORD_ZIPF
    return _word_frequency(token) >= minimum


def _common_word(token: str) -> bool:
    return token in ENGLISH_WORDS or token in KNOWN_ABBREVIATIONS or _word_frequency(token) >= COMMON_WORD_ZIPF


def _best_segmentation(text: str) -> list[str] | None:
    text = text.lower()

    @lru_cache(maxsize=None)
    def solve(position: int) -> tuple[str, ...] | None:
        if position == len(text):
            return ()
        best: tuple[str, ...] | None = None
        vocabulary = sorted(ENGLISH_WORDS | KNOWN_ABBREVIATIONS | FUNCTION_WORDS, key=len, reverse=True)
        for word in vocabulary:
            if len(word) < 3 and word not in KNOWN_ABBREVIATIONS and word not in FUNCTION_WORDS:
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
    if _known_word(text) and _word_frequency(text) >= UNCOMMON_WORD_ZIPF:
        return [text]
    ninja_tokens = [token.lower() for token in wordninja.split(text)]
    if len(ninja_tokens) >= 2 and all(_known_word(token) for token in ninja_tokens):
        return ninja_tokens
    words = sorted(ENGLISH_WORDS | KNOWN_ABBREVIATIONS | FUNCTION_WORDS, key=len, reverse=True)
    for word in words:
        if len(word) >= 3 and text.startswith(word) and len(text) - len(word) >= 3:
            return [word, text[len(word):]]
        if len(word) >= 3 and text.endswith(word) and len(text) - len(word) >= 3:
            return [text[:-len(word)], word]
    return [text]


def _real_word_ratio(tokens: list[str]) -> float:
    return sum(_known_word(token) for token in tokens) / len(tokens) if tokens else 0.0


def _generic_tokens(tokens: list[str]) -> list[str]:
    return [token for token in tokens if token in GENERIC_TERMS]


def _pronounceable_brandable(candidate: DomainCandidate, filters: FilterResult, tokens: list[str], real_ratio: float, generic_count: int = 0, trademark_risk: bool = False) -> bool:
    label = _label(candidate)
    if real_ratio >= 1 or generic_count or trademark_risk or filters.numbers or filters.hyphens or filters.awkward_spelling:
        return False
    if not 5 <= len(label) <= 10 or not 0.30 <= _vowel_ratio(label) <= 0.65 or len(tokens) > 2:
        return False
    return not bool(re.search(r"[^aeiou]{4,}", label))


def _strong_terms(tokens: list[str], candidate: DomainCandidate) -> set[str]:
    keyword_tokens = set(re.findall(r"[a-z]+", candidate.keyword.lower()))
    return (set(tokens) | keyword_tokens) & STRONG_COMMERCIAL_TERMS


def _is_natural_combination(tokens: list[str]) -> bool:
    if len(tokens) == 1:
        return _common_word(tokens[0]) or _word_frequency(tokens[0]) >= UNCOMMON_WORD_ZIPF
    if len(tokens) != 2:
        return False
    left, right = tuple(tokens)
    if tuple(tokens) in NATURAL_PAIRS:
        return True
    if not (_known_word(left) and _known_word(right)) or left in FUNCTION_WORDS or right in FUNCTION_WORDS:
        return False
    if right in GENERIC_TERMS:
        return False
    return (
        (left in CATEGORY_HEADS and right in PRODUCT_NOUNS)
        or (left in ADJECTIVE_HEADS and right in PRODUCT_NOUNS)
        or (left in VERB_HEADS and right in PRODUCT_NOUNS)
    )


def _trademark_pattern(candidate: DomainCandidate, tokens: list[str], filters: FilterResult) -> bool:
    if filters.trademark_risk:
        return True
    unknown = [token for token in tokens if not _known_word(token)]
    return bool(unknown and tokens and tokens[-1] in KNOWN_ABBREVIATIONS)


def _personal_name_like(tokens: list[str], strong: set[str]) -> bool:
    return len(tokens) == 2 and not strong and all(token in PERSONAL_NAME_TOKENS for token in tokens)


def _clamp(value: float, low: float, high: float) -> float:
    return max(low, min(high, value))


def _natural_language_score(tokens: list[str], natural: bool, real_ratio: float, pronounceable: bool) -> float:
    if len(tokens) == 1:
        if _common_word(tokens[0]):
            return 2.0
        if _known_word(tokens[0]):
            return 1.6
        return 0.8 if pronounceable else 0.0
    if len(tokens) == 2 and real_ratio == 1 and natural:
        return 1.8 if all(_common_word(token) for token in tokens) else 1.5
    if len(tokens) == 2 and real_ratio == 1:
        return 0.7
    if pronounceable:
        return 0.8
    return 0.1


def _brandability_score(candidate: DomainCandidate, filters: FilterResult, tokens: list[str], natural: bool, real_ratio: float, pronounceable: bool) -> float:
    label_length = len(_label(candidate))
    if len(tokens) == 1 and _common_word(tokens[0]) and label_length <= 10:
        return 1.9
    if len(tokens) == 1 and _known_word(tokens[0]) and label_length <= 14:
        return 1.5
    if len(tokens) == 2 and real_ratio == 1 and natural:
        return 1.6 if label_length <= 14 else 1.2
    if pronounceable:
        return 0.9
    if filters.numbers or filters.hyphens or filters.awkward_spelling:
        return 0.1
    return 0.25 if real_ratio >= 0.5 else 0.0


def _commercial_score(candidate: DomainCandidate, tokens: list[str], natural: bool, real_ratio: float, strong: set[str]) -> float:
    if not strong:
        return 0.15 if natural and real_ratio == 1 else 0.0
    score = 1.25 if real_ratio == 1 else 0.55
    if natural:
        score += 0.55
    if candidate.search_volume:
        if candidate.search_volume >= 10000:
            score += 0.2
        elif candidate.search_volume >= 1000:
            score += 0.1
    return _clamp(score, 0.0, 2.0)


def _shortness_score(candidate: DomainCandidate, filters: FilterResult, tokens: list[str]) -> float:
    length = len(_label(candidate))
    score = 1.0 if length <= 8 else 0.9 if length <= 10 else 0.7 if length <= 12 else 0.5 if length <= 15 else 0.2 if length <= 20 else 0.0
    if len(tokens) >= 3:
        score -= 0.2
    if filters.numbers or filters.hyphens or filters.awkward_spelling:
        score -= 0.2
    return _clamp(score, 0.0, 1.0)


def _keyword_score(candidate: DomainCandidate, tokens: list[str], natural: bool, real_ratio: float, strong: set[str]) -> float:
    if not tokens:
        return 0.0
    common_ratio = sum(_common_word(token) for token in tokens) / len(tokens)
    score = common_ratio * 0.7 + (0.3 if strong else 0.0)
    if natural and real_ratio == 1:
        score += 0.1
    if len(_generic_tokens(tokens)) >= 2 and not natural:
        score = min(score, 0.2)
    return _clamp(score, 0.0, 1.0)


def _resale_score(candidate: DomainCandidate, tokens: list[str], natural: bool, real_ratio: float, strong: set[str], pronounceable: bool, history: HistorySignals) -> float:
    label_length = len(_label(candidate))
    if len(tokens) == 1 and _common_word(tokens[0]) and label_length <= 10:
        base = 0.95
    elif len(tokens) == 2 and natural and real_ratio == 1 and strong:
        base = 0.9
    elif len(tokens) == 2 and natural and real_ratio == 1:
        base = 0.75
    elif pronounceable:
        base = 0.45
    else:
        base = 0.15
    history_adjustment = _clamp((float(history.historical_quality) - 4.0) / 20.0, -0.2, 0.2)
    if history.spam_like or history.suspicious_changes:
        history_adjustment -= 0.2
    return _clamp(base + history_adjustment, 0.0, 1.0)


def _broad_appeal(tokens: list[str], natural: bool, real_ratio: float, strong: set[str], filters: FilterResult, trademark_risk: bool, personal_name: bool) -> float:
    if trademark_risk or personal_name or filters.spam_signal:
        return 0.0
    if len(tokens) == 1 and _common_word(tokens[0]):
        return 0.95
    if len(tokens) == 2 and natural and real_ratio == 1:
        return 0.9 if strong else 0.7
    if real_ratio == 1 and natural:
        return 0.45
    return 0.1 if real_ratio >= 0.5 else 0.0


def _backlink_quality(candidate: DomainCandidate) -> float:
    refs = max(0.0, candidate.ref_domains or 0.0)
    links = max(0.0, candidate.backlinks or 0.0)
    return _clamp((min(7, math.log10(refs + 1) * 3.2) + min(3, math.log10(links + 1))) / 10.0, 0.0, 1.0)


def _age_history(candidate: DomainCandidate, history: HistorySignals) -> float:
    age = candidate.domain_age or 0
    score = min(3.0, age / 15.0)
    if history.first_year and history.first_year <= 2015:
        score += 2.0
    elif history.first_year:
        score += 1.0
    return _clamp(score / 5.0, 0.0, 1.0)


def _industries(candidate: DomainCandidate, tokens: list[str]) -> list[str]:
    industry_map = {
        "accounting": "accounting software", "analytics": "analytics software", "billing": "billing software",
        "commerce": "ecommerce technology", "credit": "credit and lending", "data": "data products",
        "email": "email software", "finance": "financial services", "health": "health services",
        "insurance": "insurance services", "invoice": "invoicing software", "invoices": "invoicing software",
        "ledger": "accounting and finance software", "legal": "legal services", "logistics": "logistics software",
        "marketing": "marketing software", "bank": "payments and finance", "balustrade": "architectural products",
        "gates": "security and building products", "payment": "payments software", "payments": "payments software",
        "property": "property services", "retail": "retail technology", "revenue": "revenue software",
        "sales": "sales software", "secure": "security products", "software": "software products",
        "tax": "tax software", "technology": "technology products", "trade": "trade services",
        "wallet": "payments and finance", "wealth": "wealth management",
    }
    found: list[str] = []
    for token in tokens + re.findall(r"[a-z]+", candidate.keyword.lower()):
        industry = industry_map.get(token)
        if industry and industry not in found:
            found.append(industry)
    return found[:3]


def _valuation(score: float, resale: float, ref_domains: float | None) -> tuple[str, str]:
    if score >= 9.0:
        low, high = 1500.0, 5000.0
    elif score >= 8.0:
        low, high = 800.0, 3500.0
    elif score >= 7.0:
        low, high = 500.0, 2500.0
    elif score >= 6.0:
        low, high = 150.0, 800.0
    elif score >= 5.0:
        low, high = 25.0, 300.0
    else:
        low, high = 0.0, 150.0
    multiplier = 0.75 + min(0.35, resale / 4.0) + min(0.15, math.log10((ref_domains or 0) + 1) / 25)
    low *= multiplier
    high *= multiplier
    if low <= 0:
        return "$0-$150", "$0"
    max_bid = min(MAX_MAX_BID, max(MIN_MAX_BID, low * BID_VALUE_RATIO))
    return f"${round(low / 25) * 25:,.0f}-${round(high / 25) * 25:,.0f}", f"${round(max_bid):,.0f}"


def evaluate(candidate: DomainCandidate, filters: FilterResult, history: HistorySignals) -> Evaluation:
    tokens = _semantic_tokens(candidate)
    real_ratio = _real_word_ratio(tokens)
    generic = _generic_tokens(tokens)
    natural = _is_natural_combination(tokens)
    strong = _strong_terms(tokens, candidate)
    trademark_risk = _trademark_pattern(candidate, tokens, filters)
    pronounceable = _pronounceable_brandable(candidate, filters, tokens, real_ratio, len(generic), trademark_risk)
    personal_name = _personal_name_like(tokens, strong)
    natural_language = _natural_language_score(tokens, natural, real_ratio, pronounceable)
    brandability = _brandability_score(candidate, filters, tokens, natural, real_ratio, pronounceable)
    commercial = _commercial_score(candidate, tokens, natural, real_ratio, strong)
    shortness = _shortness_score(candidate, filters, tokens)
    keyword_quality = _keyword_score(candidate, tokens, natural, real_ratio, strong)
    industries = _industries(candidate, tokens)
    resale = _resale_score(candidate, tokens, natural, real_ratio, strong, pronounceable, history)
    broad = _broad_appeal(tokens, natural, real_ratio, strong, filters, trademark_risk, personal_name)
    historical_quality = _clamp(float(history.historical_quality), 0.0, 10.0)
    backlink_quality = _backlink_quality(candidate)
    age_history = _age_history(candidate, history)
    end_user = _clamp((commercial + broad) / 2.0, 0.0, 1.0)

    penalty = 0.0
    penalty += PENALTIES["numbers"] if filters.numbers else 0.0
    penalty += PENALTIES["hyphens"] if filters.hyphens else 0.0
    penalty += PENALTIES["awkward_spelling"] if filters.awkward_spelling else 0.0
    penalty += PENALTIES["suspicious_history"] if history.suspicious_changes else 0.0
    penalty += PENALTIES["spam"] if filters.spam_signal or history.spam_like else 0.0
    penalty += PENALTIES["trademark_risk"] if trademark_risk else 0.0
    penalty += PENALTIES["weak_commercial_potential"] if not strong else 0.0
    penalty += PENALTIES["generic_suffix"] if generic and not natural else 0.0
    penalty += PENALTIES["keyword_stuffing"] if len(generic) >= 2 or len(tokens) >= 3 else 0.0
    penalty += PENALTIES["invented_string"] if real_ratio < 1 and not pronounceable else 0.0
    penalty += PENALTIES["long_three_word"] if len(tokens) >= 3 else 0.0
    penalty += PENALTIES["personal_name"] if personal_name else 0.0
    if len(tokens) == 2 and not natural and any(token in {"vibrator", "casino", "poker", "betting"} for token in tokens):
        penalty += PENALTIES["narrow_niche"]
    penalty = min(MAX_PENALTY, penalty)

    component_total = natural_language + brandability + commercial + shortness + keyword_quality + resale + broad
    score = round(_clamp(component_total - penalty, 0.0, 10.0), 1)
    class_name = classification(score)
    resale_range, bid = _valuation(score, resale, candidate.ref_domains)

    if trademark_risk:
        risk = "high"
    elif filters.spam_signal or history.spam_like:
        risk = "high"
    elif filters.awkward_spelling or real_ratio < 1:
        risk = "medium"
    else:
        risk = "low"

    recognized = [token for token in tokens if _known_word(token)]
    unknown = [token for token in tokens if not _known_word(token)]
    if len(tokens) == 1 and _common_word(tokens[0]):
        main_strength = f"Exact common English word: {tokens[0]}; strongest dictionary and buyer recall signal."
    elif len(tokens) == 1 and _known_word(tokens[0]):
        main_strength = f"Valid but less-common English term: {tokens[0]}; specific category potential requires buyer validation."
    elif len(tokens) == 2 and natural and real_ratio == 1:
        main_strength = f"Natural two-word phrase: {' '.join(tokens)}; immediately understandable business or product concept."
    elif pronounceable:
        main_strength = "Clean pronounceable brand structure; value depends on buyer adoption rather than dictionary meaning."
    elif recognized:
        main_strength = f"Readable component(s): {', '.join(recognized[:3])}; the full name does not establish a strong investor market."
    else:
        main_strength = "No recognized English or commercial word pattern supports investor-grade demand."

    weaknesses: list[str] = []
    if unknown and not pronounceable:
        weaknesses.append(f"unrecognized or invented token(s): {', '.join(unknown[:2])}")
    elif pronounceable:
        weaknesses.append("coined rather than dictionary-based; resale depends on buyer adoption")
    if len(tokens) >= 3:
        weaknesses.append("three-or-more-word structure")
    if generic and not natural:
        weaknesses.append("generic modifier or keyword-stuffed combination")
    if len(tokens) >= 2 and not natural:
        weaknesses.append(f"word combination '{' '.join(tokens)}' is not a natural phrase")
    if personal_name:
        weaknesses.append("personal-name structure with a limited buyer pool")
    if trademark_risk:
        weaknesses.append("possible trademark/company-name pattern")
    if len(_label(candidate)) > 12:
        weaknesses.append("longer or niche label may limit buyer pool")
    if filters.numbers or filters.hyphens or filters.awkward_spelling:
        weaknesses.append("spelling or formatting friction")
    main_weakness = "; ".join(weaknesses[:2]) or "Buyer breadth and resale demand still require market validation."

    reasons = [main_strength, f"Main weakness: {main_weakness}"]
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
        estimated_resale_range=resale_range,
        brandability=round(brandability, 2),
        commercial_intent=round(commercial, 2),
        keyword_quality=round(keyword_quality, 2),
        length_readability=round(shortness, 2),
        historical_quality=round(historical_quality, 2),
        backlink_quality=round(backlink_quality, 2),
        age_history=round(age_history, 2),
        end_user_potential=round(end_user, 2),
        spam_risk=risk,
        potential_industries="; ".join(industries) or "No specific end-user industry established",
        reason="; ".join(reasons),
        wayback_url=history.wayback_url,
        source=candidate.source,
        main_strength=main_strength,
        main_weakness=main_weakness,
        trademark_risk_flag="Trademark risk — manual review required." if trademark_risk else "",
        natural_language_quality=round(natural_language, 2),
        shortness_memorability=round(shortness, 2),
        resale_potential=round(resale, 2),
        broad_clean_market_appeal=round(broad, 2),
        penalty_total=round(penalty, 2),
    )
