"""
Tests the LOGIC inside KnowledgeBaseIndexer (app/vector_store.py) without
needing chromadb or sentence-transformers actually installed.

How: KnowledgeBaseIndexer accepts embed_fn and collection as constructor
arguments (dependency injection). Here we pass in a fake embed function
and a fake in-memory "collection" object that mimics ChromaDB's
.add()/.count() methods just enough to verify the indexer builds correct
ids/documents/metadatas and calls the collection correctly.

This does NOT prove the real sentence-transformers model or real
ChromaDB works — only that our code around them is correct. The real
integration must be verified locally (see README.md "What you must
verify locally").

Run with:
    python -m tests.test_vector_store_logic
"""

from app.vector_store import KnowledgeBaseIndexer


class FakeCollection:
    """Mimics just enough of ChromaDB's Collection API for this test."""

    def __init__(self):
        self.added_ids = []
        self.added_documents = []
        self.added_metadatas = []
        self.added_embeddings = []

    def count(self):
        return len(self.added_ids)

    def add(self, ids, documents, metadatas, embeddings):
        self.added_ids.extend(ids)
        self.added_documents.extend(documents)
        self.added_metadatas.extend(metadatas)
        self.added_embeddings.extend(embeddings)


def fake_embed_fn(texts):
    """Returns a fixed-size fake vector per text, instead of calling a real model."""
    return [[0.0, 1.0, 2.0] for _ in texts]


def run():
    failures = 0

    fake_collection = FakeCollection()
    indexer = KnowledgeBaseIndexer(embed_fn=fake_embed_fn, collection=fake_collection)

    count = indexer.index_knowledge_base(force_rebuild=True)
    print(f"Indexer reported {count} articles indexed.")
    print(f"Fake collection received {len(fake_collection.added_ids)} ids.")

    if count != 31 or len(fake_collection.added_ids) != 31:
        print("FAIL: expected 31 articles to be indexed")
        failures += 1
    else:
        print("PASS: all 31 articles were passed to the collection")

    required_keys = {"title", "category", "source_filename", "source_path"}
    if fake_collection.added_metadatas and not required_keys.issubset(fake_collection.added_metadatas[0].keys()):
        print(f"FAIL: metadata missing required keys, got {fake_collection.added_metadatas[0].keys()}")
        failures += 1
    else:
        print("PASS: metadata contains title, category, source_filename, source_path")

    if len(fake_collection.added_embeddings) != len(fake_collection.added_documents):
        print("FAIL: number of embeddings does not match number of documents")
        failures += 1
    else:
        print("PASS: one embedding generated per document")

    # Second call with force_rebuild=False should skip re-indexing since
    # the (fake) collection already has entries.
    second_count = indexer.index_knowledge_base(force_rebuild=False)
    if second_count != 31:
        print("FAIL: expected index_knowledge_base to skip re-indexing and report existing count")
        failures += 1
    else:
        print("PASS: re-running without --force correctly skips re-indexing")

    print("-" * 60)
    if failures == 0:
        print("ALL VECTOR STORE LOGIC TESTS PASSED (using fakes, not real chromadb/sentence-transformers)")
    else:
        print(f"{failures} TEST(S) FAILED")


if __name__ == "__main__":
    run()
