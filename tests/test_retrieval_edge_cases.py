"""
Day 14 edge-case tests for app/retrieval.py, using fake embedders and
fake collections (no real sentence-transformers/ChromaDB needed).

Focuses on cases the existing Day 5 test_retrieval_logic.py doesn't
cover: a malformed/mismatched-length vector-store response, missing
metadata, and unusual query text (unicode, HTML-like, very long).

Run with:
    python -m tests.test_retrieval_edge_cases
"""

from app.retrieval import retrieve_relevant_articles

_failures = 0


def check(condition, description):
    global _failures
    status = "PASS" if condition else "FAIL"
    print(f"[{status}] {description}")
    if not condition:
        _failures += 1


def fake_embed(texts):
    return [[0.1, 0.2, 0.3] for _ in texts]


class MismatchedLengthCollection:
    """
    Simulates a malformed vector-store response where metadatas is
    shorter than ids/documents/distances (e.g. a partial write or a
    backend bug) — not something ChromaDB should normally produce, but
    the code must not crash if it ever does.
    """
    def query(self, query_embeddings, n_results, include):
        return {
            "ids": [["a1", "a2", "a3"]],
            "documents": [["doc1", "doc2", "doc3"]],
            "metadatas": [[{"title": "T1", "category": "c1", "source_filename": "f1.md", "source_path": "/f1.md"}]],
            "distances": [[0.1, 0.2, 0.3]],
        }


class NoneMetadataCollection:
    """Simulates ChromaDB returning None for a metadata entry instead of a dict."""
    def query(self, query_embeddings, n_results, include):
        return {
            "ids": [["a1"]],
            "documents": [["doc1"]],
            "metadatas": [[None]],
            "distances": [[0.1]],
        }


class MissingKeysCollection:
    """Simulates a response missing the 'distances' key entirely."""
    def query(self, query_embeddings, n_results, include):
        return {"ids": [["a1"]], "documents": [["doc1"]], "metadatas": [[{"title": "T1", "category": "c"}]]}


class EchoQueryCollection:
    """Records exactly what query text reached the collection, for unicode/HTML/length checks."""
    def __init__(self):
        self.last_query_embedding = None

    def query(self, query_embeddings, n_results, include):
        self.last_query_embedding = query_embeddings
        return {"ids": [[]], "documents": [[]], "metadatas": [[]], "distances": [[]]}


def test_mismatched_length_result_degrades_safely():
    results = retrieve_relevant_articles("query", embed_fn=fake_embed, collection=MismatchedLengthCollection())
    check(len(results) == 1, "a response with mismatched list lengths safely truncates to the shortest list (1 usable result) instead of crashing")
    check(results[0].id == "a1" and results[0].title == "T1", "the one usable result is correctly assembled, not garbage")


def test_none_metadata_handled():
    results = retrieve_relevant_articles("query", embed_fn=fake_embed, collection=NoneMetadataCollection())
    check(len(results) == 1, "a None metadata entry does not crash retrieval")
    check(results[0].title == "" and results[0].category == "", "None metadata falls back to empty-string fields rather than raising")


def test_missing_keys_in_response_handled():
    results = retrieve_relevant_articles("query", embed_fn=fake_embed, collection=MissingKeysCollection())
    check(results == [], "a response entirely missing the 'distances' key is treated as no usable results, not a crash "
                          "(this is the same safety net that already catches a fully broken vector store)")


def test_unicode_and_html_like_query_text():
    collection = EchoQueryCollection()
    for query in ["café naïve 中文", "<script>alert(1)</script>", "'; DROP TABLE articles; --", "emoji test 🎉🔥"]:
        results = retrieve_relevant_articles(query, embed_fn=fake_embed, collection=collection)
        check(results == [], f"unusual query text {query!r} is embedded and queried without crashing (treated as plain text throughout)")
    check(collection.last_query_embedding == [[0.1, 0.2, 0.3]], "the query text reached the embedder and collection normally regardless of its content")


def test_very_long_query_handled():
    long_query = "how do I reset my password " * 2000  # ~56,000 characters
    results = retrieve_relevant_articles(long_query, embed_fn=fake_embed, collection=EchoQueryCollection())
    check(results == [], "a very long query (~56,000 characters) is handled without crashing or timing out in this test")


def run():
    tests = [
        test_mismatched_length_result_degrades_safely,
        test_none_metadata_handled,
        test_missing_keys_in_response_handled,
        test_unicode_and_html_like_query_text,
        test_very_long_query_handled,
    ]
    for test in tests:
        print(f"\n--- {test.__name__} ---")
        test()

    print("\n" + "-" * 60)
    if _failures == 0:
        print("ALL RETRIEVAL EDGE CASE TESTS PASSED (fake embedder/collection, no real chromadb/sentence-transformers)")
    else:
        print(f"{_failures} TEST(S) FAILED")


if __name__ == "__main__":
    run()
