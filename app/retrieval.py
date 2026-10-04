"""
Day 5: semantic retrieval — the layer that turns a raw user query into a
ranked, relevance-filtered list of knowledge base articles.

This module does NOT re-implement embedding or vector storage — it
reuses Day 4's embed_texts() and get_collection() from app.embeddings /
app.vector_store. Its only job is: embed the query, search, format the
results, and decide which results are actually relevant enough to use.

This is what the future RAG pipeline (feeding an LLM) will call — it
should never need to talk to ChromaDB directly.

DEPENDENCY INJECTION FOR TESTABILITY:
retrieve_relevant_articles() accepts optional embed_fn / collection
arguments, exactly like Day 4's KnowledgeBaseIndexer. This lets us test
the search/ranking/threshold LOGIC with fake data (see
tests/test_retrieval_logic.py) without needing the real
sentence-transformers model or a populated ChromaDB collection.

ENVIRONMENT NOTE: this sandbox has no internet access and does not have
sentence-transformers/chromadb installed, so real end-to-end retrieval
(real embeddings against the real indexed knowledge base) could not be
executed here. The logic itself — top-K limiting, result formatting,
threshold filtering, empty-query and error handling — was tested using
fakes (see tests/test_retrieval_logic.py) and did pass. See README.md
"Day 5" for exactly what you need to run locally to confirm real
retrieval quality.
"""

from dataclasses import dataclass
from typing import Callable, List, Optional

from app.config import DEFAULT_TOP_K, RELEVANCE_DISTANCE_THRESHOLD
from app.embeddings import embed_texts


@dataclass
class RetrievedArticle:
    """One search result, with everything the future RAG stage will need."""
    id: str
    title: str
    category: str
    content: str
    source_filename: str
    source_path: str
    distance: float       # raw distance from ChromaDB (lower = more similar)
    is_relevant: bool     # whether distance passed the relevance threshold


def _get_default_collection():
    """
    Lazily imports and fetches the real ChromaDB collection. Kept as a
    separate function (rather than imported at module load time) so this
    module can still be imported — and its logic tested — even when
    chromadb isn't installed.
    """
    from app.vector_store import get_collection
    return get_collection()


def retrieve_relevant_articles(
    query: str,
    top_k: int = DEFAULT_TOP_K,
    relevance_threshold: float = RELEVANCE_DISTANCE_THRESHOLD,
    embed_fn: Optional[Callable[[List[str]], List[List[float]]]] = None,
    collection=None,
) -> List[RetrievedArticle]:
    """
    Embeds `query`, searches the vector store for the top_k closest
    knowledge base articles, and returns them as RetrievedArticle objects
    with `is_relevant` set based on `relevance_threshold`.

    Safety behavior (by design, not accidental):
    - Empty/whitespace-only query -> returns [] immediately, without
      calling the embedding model or the vector store at all.
    - If the vector store is unavailable, unreachable, or empty, any
      error from it is caught and [] is returned — a customer-facing
      chatbot should degrade gracefully, not crash, if the knowledge
      base backend has a problem. Later days (escalation logic) will
      decide what to do when retrieval comes back empty.
    - Results are NOT dropped when they're below the relevance
      threshold — they're still returned, just marked
      `is_relevant=False`, so callers can decide (e.g. log it,
      show a "not confident" message, or trigger escalation) rather
      than silently losing information.
    """
    query = (query or "").strip()
    if not query:
        return []

    embed_fn = embed_fn or embed_texts
    if collection is None:
        try:
            collection = _get_default_collection()
        except RuntimeError:
            # chromadb not installed, or some other setup problem —
            # degrade gracefully instead of crashing the caller.
            return []

    try:
        query_embedding = embed_fn([query])[0]
        raw_results = collection.query(
            query_embeddings=[query_embedding],
            n_results=top_k,
            include=["documents", "metadatas", "distances"],
        )
    except Exception:
        # Any failure talking to the vector store (empty collection,
        # connection issue, etc.) -> no results, not a crash.
        return []

    return _format_results(raw_results, relevance_threshold)


def _format_results(raw_results: dict, relevance_threshold: float) -> List[RetrievedArticle]:
    """
    Converts ChromaDB's raw query response shape:
        {"ids": [[...]], "documents": [[...]], "metadatas": [[...]], "distances": [[...]]}
    (note the extra nesting — one list per input query; we only ever
    send one query at a time) into a flat list of RetrievedArticle.
    """
    ids = (raw_results.get("ids") or [[]])[0]
    documents = (raw_results.get("documents") or [[]])[0]
    metadatas = (raw_results.get("metadatas") or [[]])[0]
    distances = (raw_results.get("distances") or [[]])[0]

    results = []
    for article_id, content, metadata, distance in zip(ids, documents, metadatas, distances):
        metadata = metadata or {}
        results.append(
            RetrievedArticle(
                id=article_id,
                title=metadata.get("title", ""),
                category=metadata.get("category", ""),
                content=content,
                source_filename=metadata.get("source_filename", ""),
                source_path=metadata.get("source_path", ""),
                distance=distance,
                is_relevant=distance <= relevance_threshold,
            )
        )
    return results
