from __future__ import annotations

import json
from dataclasses import dataclass
from pathlib import Path
from typing import Literal, TypedDict

from langgraph.checkpoint.memory import MemorySaver
from langgraph.graph import END, START, StateGraph
from langgraph.types import interrupt

from paisa.ai.ollama_client import OllamaClient, OllamaRequestError, OllamaUnavailableError
from paisa.categorization.embeddings import LocalEmbeddingScorer
from paisa.categorization.logic import (
    AUTO_ACCEPT_THRESHOLD,
    OLLAMA_REVIEW_THRESHOLD,
    build_match_text,
    normalize_merchant_key,
    rank_category_candidates,
)
from paisa.db.queries import (
    get_category_name,
    get_merchant_rule,
    list_categories,
    record_categorization_feedback,
)


class CategorizationState(TypedDict, total=False):
    transaction_id: str
    bank: str
    date: str
    narration: str
    merchant_name: str
    amount: float
    txn_type: str
    merchant_key: str
    sample_text: str
    category_options: list[tuple[int, str]]
    candidate_categories: list[dict[str, int | float | str]]
    suggested_category_id: int | None
    suggested_category_name: str | None
    confidence: float
    source: Literal[
        "merchant-rule",
        "merchant-example",
        "starter-hints",
        "ollama-evaluator",
        "clarified",
        "default-misc",
    ]
    needs_human_review: bool
    human_resolution: dict[str, int | bool | None] | None
    should_persist_feedback: bool
    final_category_id: int | None
    final_category_name: str | None
    review_reason: str | None
@dataclass
class CategorizationGraph:
    db_path: Path
    embedding_scorer: LocalEmbeddingScorer | None = None
    ollama_client: OllamaClient | None = None
    auto_accept_threshold: float = AUTO_ACCEPT_THRESHOLD
    ollama_review_threshold: float = OLLAMA_REVIEW_THRESHOLD

    def __post_init__(self) -> None:
        self.embedding_scorer = self.embedding_scorer or LocalEmbeddingScorer()
        self.ollama_client = self.ollama_client or OllamaClient()
        graph = StateGraph(CategorizationState)
        graph.add_node("prepare_context", self.prepare_context)
        graph.add_node("exact_match", self.exact_match)
        graph.add_node("semantic_candidates", self.semantic_candidates)
        graph.add_node("ollama_evaluator", self.ollama_evaluator)
        graph.add_node("human_review_interrupt", self.human_review_interrupt)
        graph.add_node("persist_feedback", self.persist_feedback)
        graph.add_node("finalize", self.finalize)

        graph.add_edge(START, "prepare_context")
        graph.add_edge("prepare_context", "exact_match")
        graph.add_conditional_edges(
            "exact_match",
            self.route_after_exact,
            {
                "finalize": "finalize",
                "semantic_candidates": "semantic_candidates",
            },
        )
        graph.add_conditional_edges(
            "semantic_candidates",
            self.route_after_semantic,
            {
                "persist_feedback": "persist_feedback",
                "ollama_evaluator": "ollama_evaluator",
                "human_review_interrupt": "human_review_interrupt",
            },
        )
        graph.add_conditional_edges(
            "ollama_evaluator",
            self.route_after_ollama,
            {
                "persist_feedback": "persist_feedback",
                "human_review_interrupt": "human_review_interrupt",
            },
        )
        graph.add_conditional_edges(
            "human_review_interrupt",
            self.route_after_human,
            {
                "persist_feedback": "persist_feedback",
                "finalize": "finalize",
            },
        )
        graph.add_edge("persist_feedback", "finalize")
        graph.add_edge("finalize", END)
        self.compiled = graph.compile(checkpointer=MemorySaver())

    def route_after_exact(self, state: CategorizationState) -> str:
        return "finalize" if state.get("source") == "merchant-rule" else "semantic_candidates"

    def route_after_semantic(self, state: CategorizationState) -> str:
        confidence = state.get("confidence", 0.0)
        if confidence >= self.auto_accept_threshold:
            return "persist_feedback"
        if confidence >= self.ollama_review_threshold:
            return "ollama_evaluator"
        return "human_review_interrupt"

    def route_after_ollama(self, state: CategorizationState) -> str:
        return "human_review_interrupt" if state.get("needs_human_review") else "persist_feedback"

    def route_after_human(self, state: CategorizationState) -> str:
        resolution = state.get("human_resolution") or {}
        if resolution and not resolution.get("skipped"):
            return "persist_feedback"
        return "finalize"

    async def prepare_context(self, state: CategorizationState) -> CategorizationState:
        categories = await list_categories(self.db_path)
        merchant_text = state.get("merchant_name") or state["narration"]
        return {
            "merchant_key": normalize_merchant_key(merchant_text),
            "sample_text": build_match_text(state.get("merchant_name", ""), state["narration"]),
            "category_options": categories,
            "candidate_categories": [],
            "suggested_category_id": None,
            "suggested_category_name": None,
            "confidence": 0.0,
            "needs_human_review": False,
            "human_resolution": None,
            "should_persist_feedback": False,
            "final_category_id": None,
            "final_category_name": None,
            "review_reason": None,
        }

    async def exact_match(self, state: CategorizationState) -> CategorizationState:
        exact_match = await get_merchant_rule(self.db_path, state["merchant_key"])
        if exact_match is None:
            return {}

        category_id, category_name = exact_match
        return {
            "final_category_id": category_id,
            "final_category_name": category_name,
            "confidence": 1.0,
            "source": "merchant-rule",
            "should_persist_feedback": False,
        }

    async def semantic_candidates(self, state: CategorizationState) -> CategorizationState:
        match = await rank_category_candidates(
            self.db_path,
            state["sample_text"],
            embedding_scorer=self.embedding_scorer,
        )
        candidates = [
            {
                "category_id": candidate.category_id,
                "category_name": candidate.category_name,
                "score": candidate.score,
                "source": candidate.source,
            }
            for candidate in match.candidates[:5]
        ]
        update: CategorizationState = {
            "candidate_categories": candidates,
            "suggested_category_id": match.suggested_category_id,
            "suggested_category_name": match.suggested_category_name,
            "confidence": match.confidence,
            "source": match.source,
            "should_persist_feedback": match.suggested_category_id is not None
            and match.confidence >= self.auto_accept_threshold,
        }
        if match.suggested_category_id is not None and match.confidence >= self.auto_accept_threshold:
            update["final_category_id"] = match.suggested_category_id
            update["final_category_name"] = match.suggested_category_name
        return update

    async def ollama_evaluator(self, state: CategorizationState) -> CategorizationState:
        messages = [
            {
                "role": "system",
                "content": (
                    "You categorize bank transactions. Return JSON only with keys action, category_id, confidence, reason. "
                    "Allowed actions: accept or review. Pick only from provided candidates."
                ),
            },
            {
                "role": "user",
                "content": json.dumps(
                    {
                        "transaction": {
                            "merchant_name": state.get("merchant_name"),
                            "narration": state.get("narration"),
                            "sample_text": state["sample_text"],
                            "amount": state.get("amount"),
                            "type": state.get("txn_type"),
                        },
                        "candidates": state.get("candidate_categories", [])[:5],
                    }
                ),
            },
        ]

        try:
            content = await self.ollama_client.chat(messages)
            parsed = json.loads(content)
        except (json.JSONDecodeError, OllamaRequestError, OllamaUnavailableError):
            return {
                "needs_human_review": True,
                "should_persist_feedback": False,
                "review_reason": "ollama-unavailable",
            }

        if parsed.get("action") != "accept":
            return {
                "needs_human_review": True,
                "should_persist_feedback": False,
                "review_reason": str(parsed.get("reason") or "model-requested-review"),
            }

        candidate_id = parsed.get("category_id")
        valid_candidates = {
            int(candidate["category_id"]): str(candidate["category_name"])
            for candidate in state.get("candidate_categories", [])
        }
        if not isinstance(candidate_id, int) or candidate_id not in valid_candidates:
            return {
                "needs_human_review": True,
                "should_persist_feedback": False,
                "review_reason": "ollama-invalid-category",
            }

        confidence = float(parsed.get("confidence") or state.get("confidence", 0.0))
        return {
            "final_category_id": candidate_id,
            "final_category_name": valid_candidates[candidate_id],
            "confidence": confidence,
            "source": "ollama-evaluator",
            "needs_human_review": False,
            "should_persist_feedback": True,
            "review_reason": str(parsed.get("reason") or ""),
        }

    async def human_review_interrupt(self, state: CategorizationState) -> CategorizationState:
        payload = {
            "transaction_id": state["transaction_id"],
            "merchant_name": state.get("merchant_name", ""),
            "narration": state["narration"],
            "confidence": state.get("confidence", 0.0),
            "suggested_category_id": state.get("suggested_category_id"),
            "suggested_category_name": state.get("suggested_category_name"),
            "category_options": state.get("category_options", []),
            "candidate_categories": state.get("candidate_categories", []),
            "reason": state.get("review_reason"),
        }
        resolution = interrupt(payload)
        if not isinstance(resolution, dict):
            raise ValueError("Human review resolution must be a dict.")

        skipped = bool(resolution.get("skipped"))
        category_id = resolution.get("category_id")
        if skipped:
            return {
                "human_resolution": {"category_id": None, "skipped": True},
                "should_persist_feedback": False,
            }

        if not isinstance(category_id, int):
            raise ValueError("Human review category_id must be an integer when skipped is false.")

        category_ids = {category_id for category_id, _ in state.get("category_options", [])}
        category_name: str
        if category_id not in category_ids:
            try:
                category_name = await get_category_name(self.db_path, category_id)
            except LookupError as exc:
                raise ValueError(f"Unknown category_id in human review resolution: {category_id}") from exc
        else:
            category_name = await get_category_name(self.db_path, category_id)

        return {
            "human_resolution": {"category_id": category_id, "skipped": False},
            "final_category_id": category_id,
            "final_category_name": category_name,
            "source": "clarified",
            "needs_human_review": False,
            "should_persist_feedback": True,
        }

    async def persist_feedback(self, state: CategorizationState) -> CategorizationState:
        if not state.get("should_persist_feedback"):
            return {}
        if state.get("final_category_id") is None:
            raise ValueError("Cannot persist feedback without a final category.")

        await record_categorization_feedback(
            self.db_path,
            merchant_key=state["merchant_key"],
            sample_text=state["sample_text"],
            category_id=state["final_category_id"],
            transaction_id=state.get("transaction_id"),
        )
        return {}

    async def finalize(self, state: CategorizationState) -> CategorizationState:
        final_category_id = state.get("final_category_id")
        final_category_name = state.get("final_category_name")
        if final_category_id is not None and final_category_name is not None:
            return {
                "final_category_id": final_category_id,
                "final_category_name": final_category_name,
                "confidence": state.get("confidence", 0.0),
                "source": state.get("source", "default-misc"),
            }

        misc_category = next(
            ((category_id, name) for category_id, name in state["category_options"] if name == "Misc"),
            None,
        )
        if misc_category is None:
            raise LookupError("Misc category not found during finalization.")
        misc_id, misc_name = misc_category
        return {
            "final_category_id": misc_id,
            "final_category_name": misc_name,
            "confidence": state.get("confidence", 0.0),
            "source": "default-misc",
        }