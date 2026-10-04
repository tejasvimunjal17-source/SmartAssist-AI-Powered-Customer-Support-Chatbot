"""
Vector storage for the knowledge base, using ChromaDB (persistent, so the
index survives app restarts and doesn't need to be rebuilt every time).

This module has two jobs:
1. Indexing (Day 4): read articles -> embed them -> store them in ChromaDB.
2. A minimal retrieval function, just enough for Day 5 to build proper
   similarity search / top-K / relevance scoring on top of. Day 5 owns
   the real search logic — this is intentionally thin.

DESIGN NOTE FOR TESTABILITY:
KnowledgeBaseIndexer takes `embed_fn` and `collection` as constructor
arguments instead of always reaching for the real ChromaDB client and
real sentence-transformers model. In normal use (see scripts/build_index.py)
we pass in the real ones. In tests (see tests/test_vector_store_logic.py)
we pass in fake/mock versions, so the indexing LOGIC (loading articles,
building metadata, skipping empty content, etc.) can be verified without
needing ChromaDB or sentence-transformers installed at all.

ENVIRONMENT NOTE: chromadb is not installed in the sandbox this was
written in (no internet access to install it). get_collection() and any
code path that calls the real ChromaDB client could not be executed
there. The indexing LOGIC was still tested, using fakes — see the
"tests actually executed" section in the Day 4 summary.
"""

from typing import Callable, List, Optional

from app.config import CHROMA_COLLECTION_NAME, CHROMA_PERSIST_DIR
from app.embeddings import embed_texts
from app.knowledge_base_loader import KBArticle, load_articles


def get_collection():
    """
    Creates (or opens, if it already exists on disk) the persistent
    ChromaDB collection we store knowledge base embeddings in.
    """
    try:
        import chromadb
    except ImportError as exc:
        raise RuntimeError(
            "chromadb is not installed. Run: pip install chromadb"
        ) from exc

    client = chromadb.PersistentClient(path=CHROMA_PERSIST_DIR)
    return client.get_or_create_collection(name=CHROMA_COLLECTION_NAME)


class KnowledgeBaseIndexer:
    """
    Builds (or rebuilds) the vector index from the markdown knowledge
    base articles.

    embed_fn:   a function List[str] -> List[List[float]]. Defaults to
                the real sentence-transformers embedder.
    collection: an object with .add(...) and .count() methods, matching
                ChromaDB's collection API. Defaults to the real
                persistent ChromaDB collection.
    """

    def __init__(
        self,
        embed_fn: Optional[Callable[[List[str]], List[List[float]]]] = None,
        collection=None,
    ):
        self.embed_fn = embed_fn or embed_texts
        self._collection = collection  # lazy: only fetched when needed

    @property
    def collection(self):
        if self._collection is None:
            self._collection = get_collection()
        return self._collection

    def build_documents(self, articles: List[KBArticle]):
        """
        Turns KBArticle objects into the three parallel lists ChromaDB's
        .add() expects: ids, documents (text), metadatas. Skips any
        article with empty content instead of embedding nothing.
        """
        ids, documents, metadatas = [], [], []

        for article in articles:
            content = article.content.strip()
            if not content:
                continue

            ids.append(article.id)
            documents.append(content)
            metadatas.append(
                {
                    "title": article.title,
                    "category": article.category,
                    "source_filename": article.source_filename,
                    "source_path": article.source_path,
                }
            )

        return ids, documents, metadatas

    def index_knowledge_base(self, force_rebuild: bool = False) -> int:
        """
        Loads all markdown articles, embeds them, and stores them in the
        vector collection. If the collection already has entries and
        force_rebuild is False, skips re-indexing (saves time/cost on
        every app restart). Returns the number of articles indexed.
        """
        if not force_rebuild and self.collection.count() > 0:
            return self.collection.count()

        articles = load_articles()
        ids, documents, metadatas = self.build_documents(articles)

        if not ids:
            return 0

        embeddings = self.embed_fn(documents)

        self.collection.add(
            ids=ids,
            documents=documents,
            metadatas=metadatas,
            embeddings=embeddings,
        )
        return len(ids)


def retrieve(query: str, top_k: int = 3):
    """
    Minimal semantic retrieval: embeds the query and asks ChromaDB for
    the closest matching articles. This is deliberately bare-bones —
    Day 5 will add proper top-K tuning, relevance-score thresholds, and
    evaluation on top of this.
    """
    collection = get_collection()
    query_embedding = embed_texts([query])[0]
    return collection.query(query_embeddings=[query_embedding], n_results=top_k)
