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

from functools import lru_cache
from typing import List

from app.config import EMBEDDING_MODEL_NAME


@lru_cache(maxsize=1)
def get_embedder():
    """
    Loads and caches the SentenceTransformer model. Raises a clear,
    actionable error if the library isn't installed yet, instead of a
    confusing traceback.
    """
    try:
        from sentence_transformers import SentenceTransformer
    except ImportError as exc:
        raise RuntimeError(
            "sentence-transformers is not installed. Run: "
            "pip install sentence-transformers"
        ) from exc

    return SentenceTransformer(EMBEDDING_MODEL_NAME)


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
