"""Neural text embeddings: multilingual embedding generation and provider adapters."""

from __future__ import annotations

import hashlib
import logging
import threading
from collections.abc import Sequence
from typing import Any, Final, Protocol

import numpy as np

from qorvexa.knowledge.rag import extract_content_tokens, normalize_arabic

log = logging.getLogger("qorvexa.knowledge.embeddings")

DEFAULT_MULTILINGUAL_MODEL: Final[str] = "paraphrase-multilingual-MiniLM-L12-v2"
DEFAULT_EMBEDDING_DIM: Final[int] = 384


class EmbeddingError(RuntimeError):
    """Base exception for embedding generation and loading errors."""


class NeuralEmbeddingsUnavailableError(EmbeddingError):
    """Raised when the neural embedding engine (e.g. sentence-transformers) is not installed."""


class ModelLoadError(EmbeddingError):
    """Raised when the specified neural embedding model fails to load."""


class TextEmbedder(Protocol):
    """Protocol for text embedding generators."""

    @property
    def model_name(self) -> str: ...

    @property
    def dimension(self) -> int: ...

    def embed_texts(self, texts: Sequence[str]) -> np.ndarray:
        """Return L2-normalized 2D numpy array of shape (len(texts), dimension)."""
        ...

    def embed_query(self, query: str) -> np.ndarray:
        """Return L2-normalized 1D numpy array of shape (dimension,)."""
        ...


_MODEL_CACHE: dict[str, Any] = {}
_CACHE_LOCK = threading.Lock()


class SentenceTransformerEmbedder:
    """Lightweight multilingual neural embedder using sentence-transformers.

    Uses a thread-safe singleton cache across model invocations to avoid reloading weights.
    """

    def __init__(
        self,
        model_name: str = DEFAULT_MULTILINGUAL_MODEL,
        device: str = "cpu",
    ) -> None:
        self._model_name = model_name
        self._device = device
        self._dim = DEFAULT_EMBEDDING_DIM
        self._model = self._load_model()

    @property
    def model_name(self) -> str:
        return self._model_name

    @property
    def dimension(self) -> int:
        return self._dim

    def _load_model(self) -> Any:
        try:
            from sentence_transformers import SentenceTransformer  # type: ignore[import-not-found]
        except ImportError as exc:
            raise NeuralEmbeddingsUnavailableError(
                f"sentence-transformers is required to load neural model '{self._model_name}'. "
                "Install it with: pip install sentence-transformers"
            ) from exc

        with _CACHE_LOCK:
            if self._model_name not in _MODEL_CACHE:
                try:
                    log.info(
                        "Loading neural embedding model '%s' on %s",
                        self._model_name,
                        self._device,
                    )
                    model_inst = SentenceTransformer(self._model_name, device=self._device)
                    _MODEL_CACHE[self._model_name] = model_inst
                except Exception as exc:
                    raise ModelLoadError(
                        f"Failed to load neural embedding model '{self._model_name}': {exc}"
                    ) from exc
            model = _MODEL_CACHE[self._model_name]

        # Infer embedding dimension from loaded model
        try:
            self._dim = int(model.get_sentence_embedding_dimension())
        except Exception:
            self._dim = DEFAULT_EMBEDDING_DIM
        return model

    def embed_texts(self, texts: Sequence[str]) -> np.ndarray:
        if not texts:
            return np.empty((0, self._dim), dtype=np.float64)

        # Pre-normalize Arabic text to unify orthographic variants before embedding
        cleaned = [normalize_arabic(t) for t in texts]
        try:
            raw = self._model.encode(
                cleaned,
                convert_to_numpy=True,
                normalize_embeddings=True,
                show_progress_bar=False,
            )
            return np.asarray(raw, dtype=np.float64)
        except Exception as exc:
            raise EmbeddingError(f"Neural embedding generation failed: {exc}") from exc

    def embed_query(self, query: str) -> np.ndarray:
        stripped = query.strip()
        if not stripped:
            return np.zeros(self._dim, dtype=np.float64)

        cleaned = normalize_arabic(stripped)
        try:
            raw = self._model.encode(
                [cleaned],
                convert_to_numpy=True,
                normalize_embeddings=True,
                show_progress_bar=False,
            )
            return np.asarray(raw[0], dtype=np.float64)
        except Exception as exc:
            raise EmbeddingError(f"Neural query embedding generation failed: {exc}") from exc


class MockNeuralEmbedder:
    """Deterministic offline neural embedder mock for unit tests and air-gapped environments.

    Projects token semantics into an L2-normalized high-dimensional vector space without
    network access or downloading heavy model weights.
    """

    def __init__(
        self,
        model_name: str = f"mock-{DEFAULT_MULTILINGUAL_MODEL}",
        dimension: int = DEFAULT_EMBEDDING_DIM,
        *,
        fail_on_load: bool = False,
        fail_on_embed: bool = False,
    ) -> None:
        if fail_on_load:
            raise ModelLoadError(f"Simulated failure loading neural model '{model_name}'")
        self._model_name = model_name
        self._dim = dimension
        self._fail_on_embed = fail_on_embed

    @property
    def model_name(self) -> str:
        return self._model_name

    @property
    def dimension(self) -> int:
        return self._dim

    def _hash_vector(self, text: str) -> np.ndarray:
        norm_text = normalize_arabic(text)
        tokens = extract_content_tokens(norm_text)
        if not tokens:
            return np.zeros(self._dim, dtype=np.float64)

        vec = np.zeros(self._dim, dtype=np.float64)
        for tok in tokens:
            h = int(hashlib.sha256(tok.encode("utf-8")).hexdigest(), 16)
            idx1 = h % self._dim
            sign1 = 1.0 if ((h >> 16) & 1) else -1.0
            vec[idx1] += sign1

            idx2 = (h >> 8) % self._dim
            sign2 = 1.0 if ((h >> 20) & 1) else -1.0
            vec[idx2] += sign2 * 0.5

        norm = np.linalg.norm(vec)
        if norm > 0.0:
            return vec / norm
        return np.zeros(self._dim, dtype=np.float64)

    def embed_texts(self, texts: Sequence[str]) -> np.ndarray:
        if self._fail_on_embed:
            raise EmbeddingError("Simulated neural embedding computation failure")
        if not texts:
            return np.empty((0, self._dim), dtype=np.float64)
        rows = [self._hash_vector(t) for t in texts]
        return np.vstack(rows).astype(np.float64)

    def embed_query(self, query: str) -> np.ndarray:
        if self._fail_on_embed:
            raise EmbeddingError("Simulated neural embedding computation failure")
        if not query.strip():
            return np.zeros(self._dim, dtype=np.float64)
        return self._hash_vector(query)


def get_embedder(
    model_name: str | None = None,
    *,
    device: str = "cpu",
    prefer_mock: bool = False,
) -> TextEmbedder:
    """Factory for loading and caching text embedders.

    If prefer_mock is True, returns a deterministic MockNeuralEmbedder without importing
    external frameworks or downloading model weights.
    Otherwise, instantiates a SentenceTransformerEmbedder backed by a thread-safe singleton cache.
    """
    target_name = model_name or DEFAULT_MULTILINGUAL_MODEL
    if prefer_mock:
        return MockNeuralEmbedder(model_name=f"mock-{target_name}")
    return SentenceTransformerEmbedder(model_name=target_name, device=device)

