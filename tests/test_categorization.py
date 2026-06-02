from __future__ import annotations

from datetime import date

from paisa.ai.ollama_client import OllamaUnavailableError
from paisa.categorization.matcher import CategorizationService, CategoryMatchResult
from paisa.db.migrations import initialize_database
from paisa.db.queries import get_category_id
from paisa.models import (
    CategorizationCandidate,
    CategorizationPendingReview,
    CategorizationResolution,
    TransactionInput,
)


async def test_starter_hints_categorize_seeded_merchants(temp_db_path):
    await initialize_database(temp_db_path)
    service = CategorizationService(temp_db_path, similarity_threshold=0.7)

    decision = await service.categorize_transaction(
        TransactionInput(
            bank="HDFC",
            date=date(2026, 5, 1),
            narration="UPI/P2M/ZOMATO ORDER",
            merchant_name="Zomato",
            amount=280.0,
            type="DEBIT",
        )
    )

    assert decision.category_name == "Food"
    assert decision.source == "starter-hints"


async def test_low_confidence_transactions_return_prompt_and_default_misc(temp_db_path):
    await initialize_database(temp_db_path)
    service = CategorizationService(temp_db_path, similarity_threshold=0.85)
    misc_id = await get_category_id(temp_db_path, "Misc")

    decision = await service.categorize_transaction(
        TransactionInput(
            bank="ICICI",
            date=date(2026, 5, 1),
            narration="ABCD X9 INTERNAL REF",
            merchant_name="ABCD X9",
            amount=120.0,
            type="DEBIT",
        )
    )

    assert decision.category_id == misc_id
    assert decision.source == "default-misc"
    assert decision.prompt is not None
    assert decision.prompt.transaction_id


async def test_clarified_transactions_can_use_existing_category_and_learn_future_matches(temp_db_path):
    await initialize_database(temp_db_path)
    service = CategorizationService(temp_db_path, similarity_threshold=0.9)
    groceries_id = await get_category_id(temp_db_path, "Groceries")

    prompt_count = 0

    async def resolve(prompt):
        nonlocal prompt_count
        prompt_count += 1
        return CategorizationResolution(category_id=groceries_id)

    first = await service.categorize_transaction(
        TransactionInput(
            bank="HDFC",
            date=date(2026, 5, 2),
            narration="LOCAL BASKET 1452",
            merchant_name="Local Basket",
            amount=560.0,
            type="DEBIT",
        ),
        resolver=resolve,
    )

    second = await service.categorize_transaction(
        TransactionInput(
            bank="HDFC",
            date=date(2026, 5, 3),
            narration="LOCAL BASKET FRESH",
            merchant_name="Local Basket Fresh",
            amount=430.0,
            type="DEBIT",
        )
    )

    assert prompt_count == 1
    assert first.category_id == groceries_id
    assert first.source == "clarified"
    assert second.category_id == groceries_id
    assert second.source in {"merchant-rule", "merchant-example"}


async def test_new_categories_can_be_created_during_clarification(temp_db_path):
    await initialize_database(temp_db_path)
    service = CategorizationService(temp_db_path, similarity_threshold=0.9)

    decision = await service.categorize_transaction(
        TransactionInput(
            bank="ICICI",
            date=date(2026, 5, 4),
            narration="CHESS CLUB MONTHLY",
            merchant_name="Chess Club",
            amount=300.0,
            type="DEBIT",
        ),
        resolver=lambda prompt: CategorizationResolution(new_category_name="Learning"),
    )

    learned_id = await get_category_id(temp_db_path, "Learning")
    assert decision.category_id == learned_id
    assert decision.category_name == "Learning"


async def test_manual_feedback_improves_future_matches(temp_db_path):
    await initialize_database(temp_db_path)
    service = CategorizationService(temp_db_path, similarity_threshold=0.8)
    entertainment_id = await get_category_id(temp_db_path, "Entertainment")

    await service.remember_feedback(
        merchant_name="Netflix India",
        narration="NETFLIX INDIA AUTOPAY",
        category_id=entertainment_id,
        transaction_id="seed-1",
    )

    decision = await service.categorize_transaction(
        TransactionInput(
            bank="HDFC",
            date=date(2026, 5, 5),
            narration="NETFLIX SUBSCRIPTION",
            merchant_name="Netflix.com",
            amount=649.0,
            type="DEBIT",
        )
    )

    assert decision.category_id == entertainment_id
    assert decision.source == "merchant-example"


async def test_pending_review_can_resume_with_explicit_selection(temp_db_path):
    await initialize_database(temp_db_path)
    service = CategorizationService(temp_db_path, similarity_threshold=0.9)
    groceries_id = await get_category_id(temp_db_path, "Groceries")

    pending = await service.start_categorization(
        TransactionInput(
            bank="HDFC",
            date=date(2026, 5, 6),
            narration="LOCAL BASKET EXPRESS",
            merchant_name="Local Basket Express",
            amount=410.0,
            type="DEBIT",
        )
    )

    assert isinstance(pending, CategorizationPendingReview)
    assert pending.prompt.category_options

    decision = await service.resume_categorization(
        pending,
        CategorizationResolution(category_id=groceries_id),
    )

    assert decision.category_id == groceries_id
    assert decision.source == "clarified"


async def test_skipped_review_defaults_to_misc_without_learning(temp_db_path):
    await initialize_database(temp_db_path)
    service = CategorizationService(temp_db_path, similarity_threshold=0.9)
    misc_id = await get_category_id(temp_db_path, "Misc")

    pending = await service.start_categorization(
        TransactionInput(
            bank="ICICI",
            date=date(2026, 5, 7),
            narration="ODD REF ZX 991",
            merchant_name="ZX 991",
            amount=190.0,
            type="DEBIT",
        )
    )

    assert isinstance(pending, CategorizationPendingReview)

    skipped = await service.resume_categorization(
        pending,
        CategorizationResolution(skipped=True),
    )
    follow_up = await service.categorize_transaction(
        TransactionInput(
            bank="ICICI",
            date=date(2026, 5, 8),
            narration="ODD REF ZX 992",
            merchant_name="ZX 992",
            amount=210.0,
            type="DEBIT",
        )
    )

    assert skipped.category_id == misc_id
    assert skipped.source == "default-misc"
    assert follow_up.category_id == misc_id
    assert follow_up.source == "default-misc"


class _FakeOllamaClient:
    def __init__(self, response: str | None = None, error: Exception | None = None) -> None:
        self.response = response
        self.error = error

    async def chat(self, messages, *, model=None):
        if self.error is not None:
            raise self.error
        return self.response or ""


async def test_ollama_can_accept_borderline_candidate(monkeypatch, temp_db_path):
    await initialize_database(temp_db_path)
    food_id = await get_category_id(temp_db_path, "Food")

    async def fake_rank_category_candidates(db_path, sample_text, embedding_scorer=None):
        return CategoryMatchResult(
            candidates=[
                CategorizationCandidate(
                    category_id=food_id,
                    category_name="Food",
                    score=0.61,
                    source="merchant-example",
                )
            ],
            suggested_category_id=food_id,
            suggested_category_name="Food",
            confidence=0.61,
            source="merchant-example",
        )

    monkeypatch.setattr("paisa.categorization.graph.rank_category_candidates", fake_rank_category_candidates)
    service = CategorizationService(
        temp_db_path,
        similarity_threshold=0.9,
        ollama_client=_FakeOllamaClient(
            response='{"action": "accept", "category_id": %d, "confidence": 0.81, "reason": "merchant close to known food examples"}'
            % food_id
        ),
    )

    decision = await service.categorize_transaction(
        TransactionInput(
            bank="HDFC",
            date=date(2026, 5, 9),
            narration="UPI/P2M/LUNCH CORNER",
            merchant_name="Lunch Corner",
            amount=275.0,
            type="DEBIT",
        )
    )

    assert decision.category_id == food_id
    assert decision.source == "ollama-evaluator"


async def test_ollama_failure_falls_back_to_pending_review(monkeypatch, temp_db_path):
    await initialize_database(temp_db_path)
    food_id = await get_category_id(temp_db_path, "Food")

    async def fake_rank_category_candidates(db_path, sample_text, embedding_scorer=None):
        return CategoryMatchResult(
            candidates=[
                CategorizationCandidate(
                    category_id=food_id,
                    category_name="Food",
                    score=0.58,
                    source="merchant-example",
                )
            ],
            suggested_category_id=food_id,
            suggested_category_name="Food",
            confidence=0.58,
            source="merchant-example",
        )

    monkeypatch.setattr("paisa.categorization.graph.rank_category_candidates", fake_rank_category_candidates)
    service = CategorizationService(
        temp_db_path,
        similarity_threshold=0.9,
        ollama_client=_FakeOllamaClient(
            error=OllamaUnavailableError("offline"),
        ),
    )

    pending = await service.start_categorization(
        TransactionInput(
            bank="HDFC",
            date=date(2026, 5, 10),
            narration="UPI/P2M/LUNCH CORNER",
            merchant_name="Lunch Corner",
            amount=275.0,
            type="DEBIT",
        )
    )

    assert isinstance(pending, CategorizationPendingReview)
    assert pending.prompt.reason == "ollama-unavailable"
    assert pending.prompt.candidate_categories[0].category_id == food_id