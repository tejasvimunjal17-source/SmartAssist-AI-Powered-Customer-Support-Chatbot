"""
Day 11: ties together kb_admin.py (markdown file changes) and
app.vector_store.KnowledgeBaseIndexer (Day 4's embedding/ChromaDB index)
so an admin's add/edit action doesn't silently leave the RAG index
stale.

Kept as its own module (not merged into kb_admin.py) because these are
two different responsibilities: writing a markdown file vs. re-embedding
the knowledge base. This also makes both independently testable and
lets the indexer be injected for tests, exactly like every other
Day 4/5/6/7 module in this project.

ENVIRONMENT NOTE: refresh_index() calls the REAL
KnowledgeBaseIndexer.index_knowledge_base(force_rebuild=True) by
default, which needs sentence-transformers + chromadb installed — not
available in this sandbox. That real rebuild was NOT executed here. What
WAS tested: the orchestration logic itself (does create/edit correctly
call the indexer afterward, does a failure there get reported instead of
silently swallowed) — using a fake indexer, per the brief's explicit
instruction not to fake a successful real embedding rebuild.
"""

from dataclasses import dataclass
from typing import Optional

from app.admin_store import log_admin_action
from app.kb_admin import ArticleValidationError, create_article, edit_article
from app.knowledge_base_loader import KBArticle
from app.vector_store import KnowledgeBaseIndexer


@dataclass
class IndexRefreshResult:
    attempted: bool
    success: bool
    articles_indexed: int
    error: Optional[str] = None


def refresh_index(indexer: Optional[KnowledgeBaseIndexer] = None) -> IndexRefreshResult:
    """
    Forces a full re-embed/re-index of the knowledge base. Never raises —
    a failure here (e.g. chromadb/sentence-transformers not installed,
    or a real API/model problem) is reported in the result instead of
    crashing the admin request, and is recorded in the audit log either
    way so it's never silently swallowed.
    """
    indexer = indexer or KnowledgeBaseIndexer()
    try:
        count = indexer.index_knowledge_base(force_rebuild=True)
        log_admin_action("index_refresh", detail=f"{count} articles indexed", status="success")
        return IndexRefreshResult(attempted=True, success=True, articles_indexed=count)
    except Exception as exc:
        log_admin_action("index_refresh", detail=str(exc), status="failed")
        return IndexRefreshResult(attempted=True, success=False, articles_indexed=0, error=str(exc))


def add_article_and_reindex(
    title: str,
    category: str,
    content: str,
    indexer: Optional[KnowledgeBaseIndexer] = None,
) -> tuple[KBArticle, IndexRefreshResult]:
    try:
        article = create_article(title, category, content)
    except ArticleValidationError as exc:
        log_admin_action("article_create", detail=f"{title!r}", status=f"failed: {exc}")
        raise

    log_admin_action("article_create", detail=article.id, status="success")
    refresh_result = refresh_index(indexer)
    return article, refresh_result


def edit_article_and_reindex(
    article_id: str,
    new_content: str,
    indexer: Optional[KnowledgeBaseIndexer] = None,
) -> tuple[KBArticle, IndexRefreshResult]:
    try:
        article = edit_article(article_id, new_content)
    except ArticleValidationError as exc:
        log_admin_action("article_edit", detail=article_id, status=f"failed: {exc}")
        raise

    log_admin_action("article_edit", detail=article.id, status="success")
    refresh_result = refresh_index(indexer)
    return article, refresh_result
