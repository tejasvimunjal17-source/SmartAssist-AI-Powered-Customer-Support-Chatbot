"""
Tests app/evaluation.py's bookkeeping logic (does it correctly detect a
hit vs a miss?) using a FAKE retrieve_fn, so no real embeddings/ChromaDB
are needed. This does not prove real retrieval quality — only that the
evaluation framework itself correctly reports hits/misses given whatever
retrieve_fn returns.

Run with:
    python -m tests.test_evaluation_logic
"""

from app.evaluation import evaluate
from app.retrieval import RetrievedArticle

SAMPLE = [
    {"query": "forgot my password", "expected_category": "account"},
    {"query": "where is my package", "expected_category": "shipping"},
]


def make_article(category):
    return RetrievedArticle(
        id="x",
        title="Some article",
        category=category,
        content="content",
        source_filename="x.md",
        source_path="/kb/x.md",
        distance=0.3,
        is_relevant=True,
    )


def fake_retrieve_correct(query, top_k=3):
    # Always returns an article matching whichever category the query
    # is "about", based on simple keyword matching in this fake — good
    # enough to test the evaluation harness itself.
    if "password" in query:
        return [make_article("account")]
    if "package" in query:
        return [make_article("shipping")]
    return []


def fake_retrieve_always_wrong(query, top_k=3):
    return [make_article("billing")]


def fake_retrieve_empty(query, top_k=3):
    return []


def run():
    failures = 0

    results = evaluate(sample_queries=SAMPLE, retrieve_fn=fake_retrieve_correct)
    if not all(r.hit for r in results):
        print(f"FAIL: expected all hits with a correct fake retriever, got {[r.hit for r in results]}")
        failures += 1
    else:
        print("PASS: evaluation correctly marks hits when retrieval returns the expected category")

    results = evaluate(sample_queries=SAMPLE, retrieve_fn=fake_retrieve_always_wrong)
    if any(r.hit for r in results):
        print(f"FAIL: expected all misses with a wrong fake retriever, got {[r.hit for r in results]}")
        failures += 1
    else:
        print("PASS: evaluation correctly marks misses when retrieval returns the wrong category")

    results = evaluate(sample_queries=SAMPLE, retrieve_fn=fake_retrieve_empty)
    if any(r.hit for r in results) or any(r.retrieved for r in results):
        print("FAIL: expected all misses with empty results")
        failures += 1
    else:
        print("PASS: evaluation handles empty retrieval results without crashing")

    print("-" * 60)
    if failures == 0:
        print("ALL EVALUATION LOGIC TESTS PASSED (using fakes, not real retrieval)")
    else:
        print(f"{failures} TEST(S) FAILED")


if __name__ == "__main__":
    run()
