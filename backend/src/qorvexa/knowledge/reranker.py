"""Neural Cross-Encoder reranker for second-stage retrieval refinement."""

from __future__ import annotations

import hashlib
import logging
import threading
from collections.abc import Sequence
from typing import Any, Final, Protocol

from qorvexa.knowledge.rag import extract_content_tokens, normalize_arabic

log = logging.getLogger("qorvexa.knowledge.reranker")

# Lightweight multilingual Cross-Encoder trained on mMARCO covering Arabic & English (~117M params)
DEFAULT_RERANKER_MODEL: Final[str] = "cross-encoder/mmarco-mMiniLMv2-L12-H384-v1"


class RerankError(RuntimeError):
    """Base exception for reranking and model loading failures."""


class RerankerUnavailableError(RerankError):
    """Raised when sentence-transformers (CrossEncoder engine) is not installed."""


class RerankerModelLoadError(RerankError):
    """Raised when the Cross-Encoder model fails to load."""


class TextReranker(Protocol):
    """Protocol for Cross-Encoder text rerankers."""

    @property
    def model_name(self) -> str: ...

    def score_pairs(self, pairs: Sequence[tuple[str, str]]) -> list[float]:
        """Score (query, document) pairs and return scalar relevance scores."""
        ...


_RERANKER_CACHE: dict[tuple[str, str], Any] = {}
_CACHE_LOCK = threading.Lock()


class CrossEncoderReranker:
    """Multilingual neural Cross-Encoder reranker using sentence-transformers.

    Uses a thread-safe singleton cache across invocations to avoid reloading model weights.
    """

    def __init__(
        self,
        model_name: str = DEFAULT_RERANKER_MODEL,
        device: str = "cpu",
    ) -> None:
        self._model_name = model_name
        self._device = device
        self._model = self._load_model()

    @property
    def model_name(self) -> str:
        return self._model_name

    @property
    def device(self) -> str:
        return self._device

    def _load_model(self) -> Any:
        try:
            from sentence_transformers import CrossEncoder  # type: ignore[import-not-found]
        except ImportError as exc:
            raise RerankerUnavailableError(
                f"sentence-transformers is required to load Cross-Encoder model "
                f"'{self._model_name}'. Install it with: pip install sentence-transformers"
            ) from exc

        with _CACHE_LOCK:
            cache_key = (self._model_name, self._device)
            if cache_key not in _RERANKER_CACHE:
                try:
                    log.info(
                        "Loading Cross-Encoder reranker model '%s' on %s",
                        self._model_name,
                        self._device,
                    )
                    model_inst = CrossEncoder(self._model_name, device=self._device)
                    _RERANKER_CACHE[cache_key] = model_inst
                except Exception as exc:
                    raise RerankerModelLoadError(
                        f"Failed to load Cross-Encoder reranker model "
                        f"'{self._model_name}': {exc}"
                    ) from exc
            model = _RERANKER_CACHE[cache_key]
        return model

    def score_pairs(self, pairs: Sequence[tuple[str, str]]) -> list[float]:
        """Score (query, document) candidate pairs.

        Returns a list of float scores corresponding to each input pair.
        """
        if not pairs:
            return []

        cleaned_pairs = [
            (normalize_arabic(q.strip()), normalize_arabic(d.strip()))
            for q, d in pairs
        ]
        try:
            raw_scores = self._model.predict(
                cleaned_pairs,
                show_progress_bar=False,
                convert_to_numpy=True,
            )
            return [float(s) for s in raw_scores]
        except Exception as exc:
            raise RerankError(f"Cross-Encoder inference failed: {exc}") from exc


class MockCrossEncoderReranker:
    """Deterministic offline Cross-Encoder mock for unit tests and air-gapped environments.

    Computes deep token interaction and semantic overlap without downloading heavy weights
    or requiring network access.
    """

    def __init__(
        self,
        model_name: str = f"mock-{DEFAULT_RERANKER_MODEL}",
        *,
        fail_on_load: bool = False,
        fail_on_rerank: bool = False,
    ) -> None:
        if fail_on_load:
            raise RerankerModelLoadError(
                f"Simulated failure loading Cross-Encoder model '{model_name}'"
            )
        self._model_name = model_name
        self._fail_on_rerank = fail_on_rerank

    @property
    def model_name(self) -> str:
        return self._model_name

    def _score_single_pair(self, query: str, doc_text: str) -> float:
        norm_q = normalize_arabic(query.strip()).lower()
        norm_d = normalize_arabic(doc_text.strip()).lower()
        if not norm_q or not norm_d:
            return 0.0

        q_tokens = extract_content_tokens(norm_q)
        d_tokens = extract_content_tokens(norm_d)
        if not q_tokens or not d_tokens:
            return 0.0

        q_set = set(q_tokens)
        d_set = set(d_tokens)
        matched = q_set & d_set
        if not matched:
            return 0.0

        # Token coverage (fraction of query content tokens in document)
        coverage = len(matched) / len(q_set)

        # Term frequency weighting in document
        tf_match = sum(d_tokens.count(tok) for tok in matched) / len(d_tokens)

        # Exact phrase bonus
        phrase_bonus = 0.25 if norm_q in norm_d else 0.0

        # Semantic hash bonus (simulating cross-attention token alignment)
        hash_val = sum(
            int(hashlib.sha256(f"{q_tok}:{d_tok}".encode()).hexdigest(), 16) % 100
            for q_tok in matched
            for d_tok in matched
        )
        hash_factor = (hash_val % 10) / 100.0

        score = (coverage * 0.6) + min(tf_match * 0.3, 0.25) + phrase_bonus + hash_factor
        return round(min(1.0, max(0.0, score)), 4)

    def score_pairs(self, pairs: Sequence[tuple[str, str]]) -> list[float]:
        if self._fail_on_rerank:
            raise RerankError("Simulated Cross-Encoder inference computation failure")
        if not pairs:
            return []
        return [self._score_single_pair(q, d) for q, d in pairs]


def get_reranker(
    model_name: str | None = None,
    *,
    device: str = "cpu",
    prefer_mock: bool = False,
) -> TextReranker:
    """Factory for loading and caching Cross-Encoder rerankers.

    If prefer_mock is True, returns a deterministic MockCrossEncoderReranker without importing
    sentence-transformers or downloading model weights.
    Otherwise, instantiates a CrossEncoderReranker backed by a thread-safe singleton cache.
    """
    target_name = model_name or DEFAULT_RERANKER_MODEL
    if prefer_mock:
        return MockCrossEncoderReranker(model_name=f"mock-{target_name}")
    return CrossEncoderReranker(model_name=target_name, device=device)
