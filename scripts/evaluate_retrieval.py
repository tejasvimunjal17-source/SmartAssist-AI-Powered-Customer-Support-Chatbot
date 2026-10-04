"""
Run the Day 5 retrieval evaluation against the real (locally built)
knowledge base index.

Usage:
    python -m scripts.evaluate_retrieval

Requires:
    python -m scripts.build_index    (Day 4 — must be run first)
"""

from app.evaluation import evaluate, print_report

if __name__ == "__main__":
    results = evaluate()
    print_report(results)
