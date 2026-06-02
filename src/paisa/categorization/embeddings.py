from __future__ import annotations

from math import sqrt


MODEL_NAME = "all-MiniLM-L6-v2"


class EmbeddingModelUnavailable(RuntimeError):
    pass


def cosine_similarity(left: list[float], right: list[float]) -> float:
    if left is None or right is None or len(left) == 0 or len(right) == 0 or len(left) != len(right):
        return 0.0

    numerator = sum(left_value * right_value for left_value, right_value in zip(left, right, strict=True))
    left_norm = sqrt(sum(value * value for value in left))
    right_norm = sqrt(sum(value * value for value in right))
    if left_norm == 0 or right_norm == 0:
        return 0.0
    return numerator / (left_norm * right_norm)


class LocalEmbeddingScorer:
    def __init__(self, model_name: str = MODEL_NAME) -> None:
        self.model_name = model_name
        self._model = None
        self._unavailable = False

    def is_available(self) -> bool:
        if self._unavailable:
            return False
        try:
            self._get_model()
        except EmbeddingModelUnavailable:
            return False
        return True

    def score_against_many(self, text: str, candidates: list[str]) -> list[float]:
        if not candidates:
            return []

        model = self._get_model()
        embeddings = model.encode([text, *candidates], normalize_embeddings=True)
        source = embeddings[0]
        return [max(0.0, float(cosine_similarity(source, candidate))) for candidate in embeddings[1:]]

    def _get_model(self):
        if self._model is not None:
            return self._model
        if self._unavailable:
            raise EmbeddingModelUnavailable(f"Embedding model unavailable: {self.model_name}")

        try:
            from sentence_transformers import SentenceTransformer
        except ImportError as exc:
            self._unavailable = True
            raise EmbeddingModelUnavailable(f"Embedding model unavailable: {self.model_name}") from exc

        self._model = SentenceTransformer(self.model_name)
        return self._model