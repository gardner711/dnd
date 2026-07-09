"""Embedding generation using fastembed (local ONNX-based model, no GPU needed).

The model (~100 MB) is downloaded on first use and cached in ~/.cache/fastembed.
The Dockerfile pre-downloads it at build time so no internet access is needed
at runtime inside the K3s cluster.

Model: BAAI/bge-small-en-v1.5 — 384 dimensions, strong retrieval quality.
"""
from __future__ import annotations

import logging
from functools import lru_cache

import numpy as np

from app.config import settings

logger = logging.getLogger(__name__)


@lru_cache(maxsize=1)
def _get_model():
    """Load the embedding model once and cache it for the process lifetime."""
    # Lazy import so that missing fastembed at import time doesn't break tests
    # that mock this module entirely.
    from fastembed import TextEmbedding  # type: ignore[import]
    logger.info("Loading embedding model '%s'", settings.embedding_model)
    return TextEmbedding(settings.embedding_model)


def embed(text: str) -> np.ndarray:
    """Embed a single text string. Returns a 1D float32 numpy array."""
    model = _get_model()
    results = list(model.embed([text]))
    return np.array(results[0], dtype=np.float32)


def to_pg_literal(embedding: np.ndarray) -> str:
    """Format a numpy vector as a pgvector literal: '[x,y,z,...]'."""
    return f"[{','.join(f'{x:.8f}' for x in embedding.tolist())}]"
