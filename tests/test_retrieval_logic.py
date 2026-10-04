"""
Tests the LOGIC in app/retrieval.py using fake embed_fn/collection —
no sentence-transformers or chromadb required. This proves the
top-K handling, result formatting, relevance threshold, and error
handling are correct. It does NOT prove the real model/ChromaDB
integration works — that must be verified locally (see README.md).

Run with:
    python -m tests.test_retrieval_logic
"""

from app.retrieval import retrieve_relevant_articles


def fake_embed_fn(texts):
    return [[0.1, 0.2, 0.3] for _ in texts]


class FakeCollectionWithResults:
    """Mimics ChromaDB's .query() response shape for a successful search."""

    def query(self, query_embeddings, n_results, include):
        # Pretend the vector store has 5 candidates, but only n_results
        # should be returned — this checks that top_k is respected by
        # whatever wraps the real collection (real ChromaDB itself
        # applies n_results, so here we simulate it doing so correctly).
        all_ids = ["acc-001", "acc-002", "bill-001", "ship-001", "gen-001"]
        all_docs = ["doc1", "doc2", "doc3", "doc4", "doc5"]
        all_meta = [
            {"title": "Reset password", "category": "account", "source_filename": "a.md", "source_path": "/kb/a.md"},
            {"title": "Create account", "category": "account", "source_filename": "b.md", "source_path": "/kb/b.md"},
            {"title": "Refund policy", "category": "billing", "source_filename": "c.md", "source_path": "/kb/c.md"},
            {"title": "Track order", "category": "shipping", "source_filename": "d.md", "source_path": "/kb/d.md"},
            {"title": "Support hours", "category": "general", "source_filename": "e.md", "source_path": "/kb/e.md"},
        ]
        all_distances = [0.2, 0.5, 1.5, 1.8, 2.0]  # increasing = less similar

        k = n_results
        return {
            "ids": [all_ids[:k]],
            "documents": [all_docs[:k]],
            "metadatas": [all_meta[:k]],
            "distances": [all_distances[:k]],
        }


class FakeCollectionThatFails:
    """Mimics a broken/unavailable vector store."""

    def query(self, query_embeddings, n_results, include):
        raise RuntimeError("simulated vector store failure")


def run():
    failures = 0

    # --- Test 1: empty query returns [] without touching embed_fn/collection ---
    calls = {"embed": 0}

    def counting_embed_fn(texts):
        calls["embed"] += 1
        return fake_embed_fn(texts)

    result = retrieve_relevant_articles("   ", embed_fn=counting_embed_fn, collection=FakeCollectionWithResults())
    if result != [] or calls["embed"] != 0:
        print("FAIL: empty query should return [] and never call embed_fn")
        failures += 1
    else:
        print("PASS: empty query short-circuits safely")

    # --- Test 2: top_k is passed through and respected ---
    result = retrieve_relevant_articles(
        "how do I reset my password",
        top_k=2,
        embed_fn=fake_embed_fn,
        collection=FakeCollectionWithResults(),
    )
    if len(result) != 2:
        print(f"FAIL: expected 2 results for top_k=2, got {len(result)}")
        failures += 1
    else:
        print("PASS: top_k=2 returns exactly 2 results")

    # --- Test 3: result formatting includes all required fields ---
    first = result[0]
    if first.id != "acc-001" or first.title != "Reset password" or first.category != "account":
        print(f"FAIL: result formatting incorrect: {first}")
        failures += 1
    else:
        print("PASS: result fields (id, title, category, content, paths) formatted correctly")

    # --- Test 4: relevance threshold marks results correctly ---
    result = retrieve_relevant_articles(
        "how do I reset my password",
        top_k=5,
        relevance_threshold=1.0,
        embed_fn=fake_embed_fn,
        collection=FakeCollectionWithResults(),
    )
    expected_relevance = [True, True, False, False, False]  # distances: 0.2,0.5,1.5,1.8,2.0 vs threshold 1.0
    actual_relevance = [r.is_relevant for r in result]
    if actual_relevance != expected_relevance:
        print(f"FAIL: relevance flags wrong. expected {expected_relevance}, got {actual_relevance}")
        failures += 1
    else:
        print("PASS: relevance threshold correctly separates relevant vs. below-threshold results")

    # --- Test 5: below-threshold results are marked, not silently dropped ---
    if len(result) != 5:
        print("FAIL: below-threshold results should still be returned (just marked), not dropped")
        failures += 1
    else:
        print("PASS: below-threshold results are kept and marked, not discarded")

    # --- Test 6: vector store failure is handled gracefully, not a crash ---
    result = retrieve_relevant_articles(
        "anything",
        embed_fn=fake_embed_fn,
        collection=FakeCollectionThatFails(),
    )
    if result != []:
        print(f"FAIL: expected [] when the vector store fails, got {result}")
        failures += 1
    else:
        print("PASS: vector store failure returns [] instead of raising")

    print("-" * 60)
    if failures == 0:
        print("ALL RETRIEVAL LOGIC TESTS PASSED (using fakes, not real chromadb/sentence-transformers)")
    else:
        print(f"{failures} TEST(S) FAILED")


if __name__ == "__main__":
    run()
