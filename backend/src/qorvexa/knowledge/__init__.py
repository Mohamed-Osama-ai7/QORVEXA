"""Hybrid RAG, hardened PDF ingestion, typed Knowledge Graph, and retrieval evaluation."""

from qorvexa.knowledge.corpus import build_verified_corpus, extract_pdf_chunks
from qorvexa.knowledge.embeddings import (
    DEFAULT_MULTILINGUAL_MODEL,
    EmbeddingError,
    MockNeuralEmbedder,
    ModelLoadError,
    NeuralEmbeddingsUnavailableError,
    SentenceTransformerEmbedder,
    TextEmbedder,
    get_embedder,
)
from qorvexa.knowledge.eval import evaluate_retrieval_modes
from qorvexa.knowledge.kg import EPISTEMIC_LEGEND, IndustrialKnowledgeGraph
from qorvexa.knowledge.rag import HybridRagIndex, SearchMode
from qorvexa.knowledge.reranker import (
    DEFAULT_RERANKER_MODEL,
    CrossEncoderReranker,
    MockCrossEncoderReranker,
    RerankerModelLoadError,
    RerankError,
    RerankerUnavailableError,
    TextReranker,
    get_reranker,
)

__all__ = [
    "DEFAULT_MULTILINGUAL_MODEL",
    "DEFAULT_RERANKER_MODEL",
    "EPISTEMIC_LEGEND",
    "CrossEncoderReranker",
    "EmbeddingError",
    "HybridRagIndex",
    "IndustrialKnowledgeGraph",
    "MockCrossEncoderReranker",
    "MockNeuralEmbedder",
    "ModelLoadError",
    "NeuralEmbeddingsUnavailableError",
    "RerankError",
    "RerankerModelLoadError",
    "RerankerUnavailableError",
    "SearchMode",
    "SentenceTransformerEmbedder",
    "TextEmbedder",
    "TextReranker",
    "build_verified_corpus",
    "evaluate_retrieval_modes",
    "extract_pdf_chunks",
    "get_embedder",
    "get_reranker",
]
