"""
Wraps the sentence-transformers embedding model specified in the official
brief (all-MiniLM-L6-v2).

Like app/preprocessing.py's spaCy model, the model is loaded lazily and
cached — downloading/loading it is slow (and requires internet the first
time), so we do it once, not on every call.

IMPORTANT (environment note): sentence-transformers is NOT installed in
the sandbox this code was written in, and that sandbox has no internet
access to install it or download the model. This file was written and
syntax-checked, but get_embedder()/embed_texts() could not actually be
executed there. You must run it yourself locally — see README.md.
"""

import threading
from typing import List

from app.config import EMBEDDING_MODEL_NAME


_embedder = None
_embedder_lock = threading.Lock()


def get_embedder():
    """
    Loads and caches the SentenceTransformer model ONCE per process.
    Thread-safe: the startup warm-up thread and an early customer request
    can arrive together, and lru_cache alone would let both load the model
    (double the memory at the worst moment). Raises a clear, actionable
    error if the library isn't installed.
    """
    global _embedder
    if _embedder is not None:
        return _embedder
    with _embedder_lock:
        if _embedder is None:
            try:
                from sentence_transformers import SentenceTransformer
            except ImportError as exc:
                raise RuntimeError(
                    "sentence-transformers is not installed. Run: "
                    "pip install sentence-transformers"
                ) from exc
            _embedder = SentenceTransformer(EMBEDDING_MODEL_NAME)
    return _embedder


def embed_texts(texts: List[str]) -> List[List[float]]:
    """
    Converts a list of strings into a list of embedding vectors.
    Empty input returns an empty list instead of calling the model.
    """
    if not texts:
        return []

    model = get_embedder()
    vectors = model.encode(list(texts), show_progress_bar=False)
    # .tolist() so callers (e.g. ChromaDB) get plain Python lists,
    # not numpy arrays.
    return [vector.tolist() for vector in vectors]


def warm_up() -> bool:
    """
    Loads the model and runs one tiny encode so the first real request
    doesn't pay for model load + first-call initialisation. Returns True
    on success, False (never raises) if the model is unavailable.
    """
    try:
        embed_texts(["warm up"])
        return True
    except Exception:
        return False
