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

__all__ = [
    "DEFAULT_MULTILINGUAL_MODEL",
    "EPISTEMIC_LEGEND",
    "EmbeddingError",
    "HybridRagIndex",
    "IndustrialKnowledgeGraph",
    "MockNeuralEmbedder",
    "ModelLoadError",
    "NeuralEmbeddingsUnavailableError",
    "SearchMode",
    "SentenceTransformerEmbedder",
    "TextEmbedder",
    "build_verified_corpus",
    "evaluate_retrieval_modes",
    "extract_pdf_chunks",
    "get_embedder",
]
