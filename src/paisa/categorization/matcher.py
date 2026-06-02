from __future__ import annotations

import inspect
from collections.abc import Awaitable, Callable
from dataclasses import replace
from pathlib import Path

from langgraph.types import Command

from paisa.ai.ollama_client import OllamaClient
from paisa.categorization.graph import CategorizationGraph, CategorizationState
from paisa.categorization.embeddings import EmbeddingModelUnavailable, LocalEmbeddingScorer
from paisa.categorization.logic import (
    CategoryMatchResult,
    build_match_text,
    hint_similarity,
    normalize_merchant_key,
    rank_category_candidates,
    similarity_score,
)
from paisa.db.queries import (
    ensure_category,
    get_merchant_rule,
    list_categories,
    list_category_examples,
    record_categorization_feedback,
)
from paisa.models import (
    CategorizationCandidate,
    CategorizationDecision,
    CategorizationPendingReview,
    CategorizationPrompt,
    CategorizationResolution,
    TransactionInput,
)
from paisa.parsers.merchant_cleaner import clean_merchant_name


CategorizationResolver = Callable[
    [CategorizationPrompt],
    CategorizationResolution | Awaitable[CategorizationResolution | None] | None,
]



class CategorizationService:
    def __init__(
        self,
        db_path: Path,
        similarity_threshold: float = 0.72,
        embedding_scorer: LocalEmbeddingScorer | None = None,
        ollama_client: OllamaClient | None = None,
    ) -> None:
        self.db_path = db_path
        self.similarity_threshold = similarity_threshold
        self.embedding_scorer = embedding_scorer or LocalEmbeddingScorer()
        self.graph = CategorizationGraph(
            db_path,
            embedding_scorer=self.embedding_scorer,
            ollama_client=ollama_client,
            auto_accept_threshold=similarity_threshold,
        )

    async def categorize_transaction(
        self,
        transaction: TransactionInput,
        resolver: CategorizationResolver | None = None,
    ) -> CategorizationDecision:
        result = await self.start_categorization(transaction)
        if isinstance(result, CategorizationDecision):
            return result

        resolution = await self._resolve_prompt(result.prompt, resolver)
        if resolution is None:
            resolution = CategorizationResolution(skipped=True)
        return await self.resume_categorization(result, resolution)

    async def categorize_transactions(
        self,
        transactions: list[TransactionInput],
        resolver: CategorizationResolver | None = None,
    ) -> list[TransactionInput]:
        categorized: list[TransactionInput] = []
        for transaction in transactions:
            decision = await self.categorize_transaction(transaction, resolver=resolver)
            categorized.append(replace(transaction, category_id=decision.category_id))
        return categorized

    async def remember_feedback(
        self,
        merchant_name: str,
        narration: str,
        category_id: int,
        transaction_id: str | None = None,
    ) -> None:
        merchant_key = normalize_merchant_key(merchant_name or narration)
        sample_text = build_match_text(merchant_name, narration)
        await record_categorization_feedback(
            self.db_path,
            merchant_key=merchant_key,
            sample_text=sample_text,
            category_id=category_id,
            transaction_id=transaction_id,
        )

    async def start_categorization(
        self,
        transaction: TransactionInput,
        *,
        thread_id: str | None = None,
    ) -> CategorizationDecision | CategorizationPendingReview:
        run_thread_id = thread_id or f"categorization:{transaction.id}"
        state = await self.graph.compiled.ainvoke(
            self._build_initial_state(transaction),
            config=self._graph_config(run_thread_id),
        )
        interrupt_payload = self._interrupt_payload(state)
        if interrupt_payload is not None:
            return CategorizationPendingReview(
                thread_id=run_thread_id,
                prompt=self._prompt_from_payload(interrupt_payload),
            )
        return self._decision_from_state(state)

    async def resume_categorization(
        self,
        pending_review: CategorizationPendingReview,
        resolution: CategorizationResolution,
    ) -> CategorizationDecision:
        resume_resolution = await self._resume_payload(pending_review.prompt, resolution)
        state = await self.graph.compiled.ainvoke(
            Command(resume=resume_resolution),
            config=self._graph_config(pending_review.thread_id),
        )
        return self._decision_from_state(state, prompt=pending_review.prompt)

    async def _pick_best_category(self, sample_text: str) -> tuple[int | None, str | None, float, str]:
        match = await rank_category_candidates(
            self.db_path,
            sample_text,
            embedding_scorer=self.embedding_scorer,
        )
        return (
            match.suggested_category_id,
            match.suggested_category_name,
            match.confidence,
            match.source,
        )

    async def _resolve_prompt(
        self,
        prompt: CategorizationPrompt,
        resolver: CategorizationResolver | None,
    ) -> CategorizationResolution | None:
        if resolver is None:
            return None

        resolution = resolver(prompt)
        if inspect.isawaitable(resolution):
            awaited = await resolution
            return awaited
        return resolution

    def _hint_similarity(self, sample_text: str, category_name: str) -> float:
        return hint_similarity(sample_text, category_name, self.embedding_scorer)

    def _similarity(self, left: str, right: str) -> float:
        return similarity_score(left, right, self.embedding_scorer)

    def _build_initial_state(self, transaction: TransactionInput) -> CategorizationState:
        return {
            "transaction_id": transaction.id,
            "bank": transaction.bank,
            "date": transaction.date.isoformat(),
            "narration": transaction.narration,
            "merchant_name": transaction.merchant_name,
            "amount": transaction.amount,
            "txn_type": transaction.type,
        }

    def _graph_config(self, thread_id: str) -> dict[str, dict[str, str]]:
        return {"configurable": {"thread_id": thread_id}}

    def _interrupt_payload(self, state: object) -> dict[str, object] | None:
        if not isinstance(state, dict):
            return None
        interrupts = state.get("__interrupt__")
        if not isinstance(interrupts, tuple | list) or not interrupts:
            return None
        payload = getattr(interrupts[0], "value", interrupts[0])
        if not isinstance(payload, dict):
            raise ValueError("Interrupt payload must be a dict.")
        return payload

    def _prompt_from_payload(self, payload: dict[str, object]) -> CategorizationPrompt:
        raw_candidates = payload.get("candidate_categories")
        candidates: list[CategorizationCandidate] = []
        if isinstance(raw_candidates, list):
            for candidate in raw_candidates:
                if not isinstance(candidate, dict):
                    continue
                category_id = candidate.get("category_id")
                category_name = candidate.get("category_name")
                score = candidate.get("score")
                source = candidate.get("source")
                if (
                    isinstance(category_id, int)
                    and isinstance(category_name, str)
                    and isinstance(score, int | float)
                    and isinstance(source, str)
                ):
                    candidates.append(
                        CategorizationCandidate(
                            category_id=category_id,
                            category_name=category_name,
                            score=float(score),
                            source=source,
                        )
                    )

        raw_category_options = payload.get("category_options")
        category_options: list[tuple[int, str]] = []
        if isinstance(raw_category_options, list):
            for option in raw_category_options:
                if isinstance(option, tuple) and len(option) == 2 and isinstance(option[0], int) and isinstance(option[1], str):
                    category_options.append(option)
                    continue
                if isinstance(option, list) and len(option) == 2 and isinstance(option[0], int) and isinstance(option[1], str):
                    category_options.append((option[0], option[1]))

        return CategorizationPrompt(
            transaction_id=str(payload["transaction_id"]),
            merchant_name=str(payload.get("merchant_name") or ""),
            narration=str(payload.get("narration") or ""),
            confidence=float(payload.get("confidence") or 0.0),
            suggested_category_id=payload.get("suggested_category_id") if isinstance(payload.get("suggested_category_id"), int) else None,
            suggested_category_name=(
                str(payload.get("suggested_category_name"))
                if isinstance(payload.get("suggested_category_name"), str)
                else None
            ),
            category_options=category_options,
            candidate_categories=candidates,
            reason=str(payload.get("reason")) if isinstance(payload.get("reason"), str) else None,
        )

    async def _resume_payload(
        self,
        prompt: CategorizationPrompt,
        resolution: CategorizationResolution,
    ) -> dict[str, int | bool | None]:
        if resolution.skipped:
            return {"category_id": None, "skipped": True}

        resolved_category_id = resolution.category_id
        if resolution.new_category_name:
            resolved_category_id = await ensure_category(self.db_path, resolution.new_category_name)
        if resolved_category_id is None:
            resolved_category_id = next(
                category_id for category_id, name in prompt.category_options if name == "Misc"
            )
        return {"category_id": resolved_category_id, "skipped": False}

    def _decision_from_state(
        self,
        state: object,
        *,
        prompt: CategorizationPrompt | None = None,
    ) -> CategorizationDecision:
        if not isinstance(state, dict):
            raise ValueError("Final graph state must be a dict.")
        category_id = state.get("final_category_id")
        category_name = state.get("final_category_name")
        confidence = state.get("confidence")
        source = state.get("source")
        if not isinstance(category_id, int) or not isinstance(category_name, str):
            raise ValueError("Final graph state is missing category output.")
        if not isinstance(confidence, int | float) or not isinstance(source, str):
            raise ValueError("Final graph state is missing decision metadata.")
        return CategorizationDecision(
            category_id=category_id,
            category_name=category_name,
            confidence=float(confidence),
            source=source,
            prompt=prompt,
        )