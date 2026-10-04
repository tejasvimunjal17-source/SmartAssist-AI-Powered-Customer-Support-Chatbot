"""
Day 5 retrieval evaluation.

This runs a small set of representative queries — one or more per
knowledge base category — through retrieve_relevant_articles() and
reports whether the EXPECTED category actually showed up in the
results. This does not grade response quality (that needs the LLM,
which comes later) — it only checks whether semantic search is finding
the right kind of article at all.

Run locally with:
    python -m scripts.evaluate_retrieval

IMPORTANT: this requires the Day 4 index to already be built
(python -m scripts.build_index) and sentence-transformers/chromadb to
be installed — neither is available in the sandbox this was written in,
so the real numbers below could NOT be generated here. Running this is
one of the things you need to do locally. No performance numbers are
invented or assumed in this file.
"""

from dataclasses import dataclass
from typing import Callable, List, Optional

from app.retrieval import RetrievedArticle, retrieve_relevant_articles

# One or more sample queries per existing knowledge base category.
# Deliberately phrased in a customer's own words, not KB article titles,
# so this actually tests SEMANTIC matching rather than keyword overlap.
SAMPLE_QUERIES = [
    {"query": "I forgot my password and can't log in", "expected_category": "account"},
    {"query": "how do I close my account", "expected_category": "account"},
    {"query": "why did you charge my card two times", "expected_category": "billing"},
    {"query": "can I get my money back", "expected_category": "billing"},
    {"query": "where is my package right now", "expected_category": "shipping"},
    {"query": "does delivery work outside the country", "expected_category": "shipping"},
    {"query": "I want to send this item back", "expected_category": "returns"},
    {"query": "how long until I get refunded for a return", "expected_category": "returns"},
    {"query": "the website keeps freezing", "expected_category": "technical"},
    {"query": "I got logged out for no reason", "expected_category": "technical"},
    {"query": "what time is your support team available", "expected_category": "general"},
    {"query": "can I speak to a real person", "expected_category": "general"},
]


@dataclass
class EvalResult:
    query: str
    expected_category: str
    retrieved: List[RetrievedArticle]
    hit: bool  # True if expected_category appears anywhere in the retrieved results


def evaluate(
    sample_queries=SAMPLE_QUERIES,
    top_k: int = 3,
    retrieve_fn: Optional[Callable[..., List[RetrievedArticle]]] = None,
) -> List[EvalResult]:
    """
    Runs every sample query through retrieval and records a hit/miss.

    retrieve_fn defaults to the real retrieve_relevant_articles, but can
    be swapped for a fake in tests (see tests/test_evaluation_logic.py)
    so this function's own bookkeeping logic can be verified without
    needing the real model/vector store.
    """
    retrieve_fn = retrieve_fn or retrieve_relevant_articles
    results = []

    for case in sample_queries:
        retrieved = retrieve_fn(case["query"], top_k=top_k)
        hit = any(article.category == case["expected_category"] for article in retrieved)
        results.append(
            EvalResult(
                query=case["query"],
                expected_category=case["expected_category"],
                retrieved=retrieved,
                hit=hit,
            )
        )

    return results


def print_report(results: List[EvalResult]) -> None:
    hits = sum(1 for r in results if r.hit)
    print(f"Retrieval evaluation: {hits}/{len(results)} queries retrieved the expected category\n")

    for r in results:
        status = "HIT " if r.hit else "MISS"
        print(f"[{status}] query={r.query!r} expected_category={r.expected_category!r}")
        if not r.retrieved:
            print("         -> no results returned")
        for article in r.retrieved:
            relevance = "relevant" if article.is_relevant else "below threshold"
            print(
                f"         -> [{article.category}] {article.title!r} "
                f"(distance={article.distance:.4f}, {relevance})"
            )
        print()


if __name__ == "__main__":
    report = evaluate()
    print_report(report)
