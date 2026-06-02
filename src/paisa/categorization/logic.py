from __future__ import annotations

import re
from dataclasses import dataclass
from difflib import SequenceMatcher
from pathlib import Path

from paisa.categorization.embeddings import EmbeddingModelUnavailable, LocalEmbeddingScorer
from paisa.db.queries import list_categories, list_category_examples
from paisa.models import CategorizationCandidate
from paisa.parsers.merchant_cleaner import clean_merchant_name


COMMON_WORDS = {
    "bank",
    "india",
    "payment",
    "pay",
    "private",
    "limited",
    "ltd",
    "services",
    "service",
    "store",
    "txn",
    "transaction",
}

AUTO_ACCEPT_THRESHOLD = 0.72
OLLAMA_REVIEW_THRESHOLD = 0.45

STARTER_CATEGORY_HINTS: dict[str, tuple[str, ...]] = {
    "Food": ("zomato", "swiggy", "restaurant", "cafe", "coffee", "dine", "pizza"),
    "Groceries": ("grocery", "grocer", "mart", "supermarket", "instamart", "blinkit"),
    "Utilities": ("electricity", "water", "broadband", "recharge", "mobile", "gas"),
    "Rent": ("rent", "landlord", "lease", "apartment"),
    "Travel": ("uber", "ola", "air", "rail", "metro", "fuel", "petrol", "diesel"),
    "Shopping": ("amazon", "flipkart", "myntra", "ajio", "store", "mall"),
    "Income": ("salary", "payroll", "bonus", "interest", "refund"),
    "Transfer": ("transfer", "self", "upi", "neft", "imps", "rtgs"),
    "Healthcare": ("hospital", "clinic", "pharmacy", "medical", "doctor"),
    "Entertainment": ("netflix", "spotify", "prime", "movie", "bookmyshow", "gaming"),
    "Misc": (),
}


@dataclass(frozen=True)
class CategoryMatchResult:
    candidates: list[CategorizationCandidate]
    suggested_category_id: int | None
    suggested_category_name: str | None
    confidence: float
    source: str


def hint_similarity(
    sample_text: str,
    category_name: str,
    embedding_scorer: LocalEmbeddingScorer | None = None,
) -> float:
    base_similarity = similarity_score(sample_text, category_name, embedding_scorer)
    hint_matches = 0
    sample_text_lower = sample_text.lower()
    for keyword in STARTER_CATEGORY_HINTS.get(category_name, ()): 
        if keyword in sample_text_lower:
            hint_matches += 1
    if hint_matches == 0:
        return base_similarity
    boosted_similarity = 0.66 + min(0.12 * hint_matches, 0.24)
    return max(base_similarity, boosted_similarity)


def similarity_score(
    left: str,
    right: str,
    embedding_scorer: LocalEmbeddingScorer | None = None,
) -> float:
    left_token_list = tokenize(left)
    right_token_list = tokenize(right)
    left_tokens = set(left_token_list)
    right_tokens = set(right_token_list)

    token_similarity = 0.0
    if left_tokens and right_tokens:
        overlap = len(left_tokens & right_tokens)
        union = len(left_tokens | right_tokens)
        token_similarity = overlap / union if union else 0.0

    sequence_similarity = SequenceMatcher(None, left.lower(), right.lower()).ratio()
    similarity = max(token_similarity, sequence_similarity * 0.85)

    shared_tokens = left_tokens & right_tokens
    if shared_tokens:
        overlap_boost = 0.64 + min(0.1 * len(shared_tokens), 0.22)
        if left_token_list and right_token_list and left_token_list[0] == right_token_list[0]:
            overlap_boost = max(overlap_boost + 0.12, 0.92)
        similarity = max(similarity, min(overlap_boost, 0.96))

    embedding_scores: list[float] = []
    if embedding_scorer is not None:
        try:
            embedding_scores = embedding_scorer.score_against_many(left, [right])
        except EmbeddingModelUnavailable:
            embedding_scores = []

    if embedding_scores:
        similarity = max(similarity, embedding_scores[0])

    return similarity


async def rank_category_candidates(
    db_path: Path,
    sample_text: str,
    embedding_scorer: LocalEmbeddingScorer | None = None,
) -> CategoryMatchResult:
    examples = await list_category_examples(db_path)
    categories = await list_categories(db_path)
    category_names = dict(categories)
    scored_candidates: dict[int, CategorizationCandidate] = {}

    for example in examples:
        score = similarity_score(sample_text, example.sample_text, embedding_scorer)
        current = scored_candidates.get(example.category_id)
        if current is None or score > current.score:
            scored_candidates[example.category_id] = CategorizationCandidate(
                category_id=example.category_id,
                category_name=example.category_name,
                score=min(score, 0.99),
                source="merchant-example",
            )

    for category_id, category_name in categories:
        score = hint_similarity(sample_text, category_name, embedding_scorer)
        current = scored_candidates.get(category_id)
        if current is None or score > current.score:
            scored_candidates[category_id] = CategorizationCandidate(
                category_id=category_id,
                category_name=category_name,
                score=min(score, 0.99),
                source="starter-hints",
            )

    ranked_candidates = sorted(
        scored_candidates.values(),
        key=lambda candidate: (-candidate.score, candidate.category_name.lower()),
    )

    if not ranked_candidates:
        return CategoryMatchResult(
            candidates=[],
            suggested_category_id=None,
            suggested_category_name=None,
            confidence=0.0,
            source="starter-hints",
        )

    best_candidate = ranked_candidates[0]
    return CategoryMatchResult(
        candidates=ranked_candidates,
        suggested_category_id=best_candidate.category_id,
        suggested_category_name=category_names[best_candidate.category_id],
        confidence=best_candidate.score,
        source=best_candidate.source,
    )


def build_match_text(merchant_name: str, narration: str) -> str:
    cleaned_narration = clean_merchant_name(narration)
    merged = " ".join(part for part in (merchant_name.strip(), cleaned_narration) if part).strip()
    return re.sub(r"\s+", " ", merged)


def normalize_merchant_key(text: str) -> str:
    tokens = [token for token in tokenize(clean_merchant_name(text)) if token not in COMMON_WORDS]
    if not tokens:
        return "unknown"
    return " ".join(tokens[:4])


def tokenize(text: str) -> list[str]:
    return [token for token in re.findall(r"[a-z0-9]+", text.lower()) if len(token) > 1]