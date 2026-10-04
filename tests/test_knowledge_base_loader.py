"""
Tests app/knowledge_base_loader.py against the REAL knowledge_base/
folder. No sentence-transformers or chromadb needed — this only checks
that the 31 markdown articles parse correctly.

Run with:
    python -m tests.test_knowledge_base_loader
"""

from app.knowledge_base_loader import load_articles

EXPECTED_MIN_ARTICLES = 30
EXPECTED_CATEGORIES = {"account", "billing", "technical", "shipping", "returns", "general"}


def run():
    failures = 0
    articles = load_articles()

    print(f"Loaded {len(articles)} articles.")

    if len(articles) < EXPECTED_MIN_ARTICLES:
        print(f"FAIL: expected at least {EXPECTED_MIN_ARTICLES} articles, got {len(articles)}")
        failures += 1
    else:
        print("PASS: article count >= 30")

    found_categories = {a.category for a in articles}
    missing = EXPECTED_CATEGORIES - found_categories
    if missing:
        print(f"FAIL: missing categories: {missing}")
        failures += 1
    else:
        print(f"PASS: all expected categories present ({sorted(found_categories)})")

    empty_content = [a.id for a in articles if not a.content.strip()]
    if empty_content:
        print(f"FAIL: these articles have empty content: {empty_content}")
        failures += 1
    else:
        print("PASS: no article has empty content")

    missing_titles = [a.source_filename for a in articles if a.title == a.source_filename]
    if missing_titles:
        print(f"FAIL: these articles failed to parse a title from frontmatter: {missing_titles}")
        failures += 1
    else:
        print("PASS: every article has a proper title parsed from frontmatter")

    print("-" * 60)
    if failures == 0:
        print("ALL KNOWLEDGE BASE LOADER TESTS PASSED")
    else:
        print(f"{failures} TEST(S) FAILED")


if __name__ == "__main__":
    run()
