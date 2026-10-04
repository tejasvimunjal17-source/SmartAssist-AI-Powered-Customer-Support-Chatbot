"""
Run this once (and again whenever knowledge_base/ articles change) to
build the ChromaDB vector index.

Usage:
    python -m scripts.build_index
    python -m scripts.build_index --force     # rebuild even if already indexed
"""

import argparse

from app.vector_store import KnowledgeBaseIndexer


def main():
    parser = argparse.ArgumentParser(description="Build the SmartAssist knowledge base vector index.")
    parser.add_argument(
        "--force",
        action="store_true",
        help="Rebuild the index even if it already has entries.",
    )
    args = parser.parse_args()

    indexer = KnowledgeBaseIndexer()
    count = indexer.index_knowledge_base(force_rebuild=args.force)
    print(f"Indexed {count} articles into ChromaDB.")


if __name__ == "__main__":
    main()
