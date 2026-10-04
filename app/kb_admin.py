"""
Day 11: create/edit knowledge base markdown articles safely.

SECURITY — this is the most important part of this file:
Both `category` and the slug generated from `title` are validated
against SAFE_NAME_PATTERN (lowercase letters/digits/hyphens only) BEFORE
they are ever used to build a filesystem path. That pattern cannot
contain "/", "\\", "..", or any other path-control character, which is
what makes path traversal structurally impossible here — not just
"unlikely". As defense-in-depth on top of that, every resulting path is
also verified with os.path.realpath() to confirm it still resolves
inside KNOWLEDGE_BASE_DIR before any file is written.

This module does NOT touch the embedding/vector-store index — see
app/admin_service.py for the orchestration that calls this AND then
triggers a re-index, kept separate on purpose (single responsibility:
this file only knows about markdown files on disk).
"""

import os
import re
from dataclasses import dataclass
from typing import List, Optional

from app.config import (
    KNOWLEDGE_BASE_DIR,
    MAX_ARTICLE_CONTENT_CHARS,
    MAX_ARTICLE_TITLE_LENGTH,
    SAFE_NAME_PATTERN,
)
from app.knowledge_base_loader import KBArticle, load_articles

_SAFE_NAME_RE = re.compile(SAFE_NAME_PATTERN)


class ArticleValidationError(ValueError):
    """Raised when submitted article data fails validation."""


def slugify(title: str) -> str:
    """Turns a title into a safe filename slug (lowercase, hyphens only)."""
    slug = title.strip().lower()
    slug = re.sub(r"[^a-z0-9]+", "-", slug)
    slug = slug.strip("-")
    return slug or "article"


def _validate_within_kb_dir(path: str) -> str:
    """
    Defense-in-depth: confirms the resolved absolute path is actually
    inside KNOWLEDGE_BASE_DIR. Raises ArticleValidationError otherwise.
    Returns the validated real path.
    """
    real_kb_dir = os.path.realpath(KNOWLEDGE_BASE_DIR)
    real_path = os.path.realpath(path)
    if not (real_path == real_kb_dir or real_path.startswith(real_kb_dir + os.sep)):
        raise ArticleValidationError("Resolved path escapes the knowledge base directory")
    return real_path


def validate_article_fields(title: str, category: str, content: str) -> None:
    title = (title or "").strip()
    category = (category or "").strip()
    content = (content or "").strip()

    if not title:
        raise ArticleValidationError("Title is required")
    if len(title) > MAX_ARTICLE_TITLE_LENGTH:
        raise ArticleValidationError(f"Title must be {MAX_ARTICLE_TITLE_LENGTH} characters or fewer")

    if not category:
        raise ArticleValidationError("Category is required")
    if not _SAFE_NAME_RE.match(category):
        raise ArticleValidationError(
            "Category must be lowercase letters, numbers, and hyphens only (e.g. 'billing')"
        )

    if not content:
        raise ArticleValidationError("Content is required")
    if len(content) > MAX_ARTICLE_CONTENT_CHARS:
        raise ArticleValidationError(f"Content must be {MAX_ARTICLE_CONTENT_CHARS} characters or fewer")


def _write_article_file(path: str, article_id: str, category: str, title: str, content: str) -> None:
    file_content = (
        "---\n"
        f"id: {article_id}\n"
        f"category: {category}\n"
        f'title: "{title}"\n'
        "---\n\n"
        f"# {title}\n\n"
        f"{content}\n"
    )
    with open(path, "w", encoding="utf-8") as f:
        f.write(file_content)


def create_article(title: str, category: str, content: str) -> KBArticle:
    """
    Creates a new markdown article. Refuses to overwrite an existing
    file with the same slug in the same category (use edit_article for
    that instead) — an accidental duplicate title should not silently
    destroy an existing article.
    """
    validate_article_fields(title, category, content)
    title = title.strip()
    category = category.strip()
    content = content.strip()

    slug = slugify(title)
    if not _SAFE_NAME_RE.match(slug):
        raise ArticleValidationError("Title does not produce a safe filename — please use more standard characters")

    category_dir = _validate_within_kb_dir(os.path.join(KNOWLEDGE_BASE_DIR, category))
    os.makedirs(category_dir, exist_ok=True)

    file_path = _validate_within_kb_dir(os.path.join(category_dir, f"{slug}.md"))
    if os.path.exists(file_path):
        raise ArticleValidationError(
            f"An article with this title already exists in '{category}' — use edit instead"
        )

    article_id = f"{category}-{slug}"
    _write_article_file(file_path, article_id, category, title, content)

    return KBArticle(
        id=article_id,
        category=category,
        title=title,
        content=content,
        source_filename=f"{slug}.md",
        source_path=file_path,
    )


def get_article(article_id: str) -> Optional[KBArticle]:
    for article in load_articles(kb_dir=KNOWLEDGE_BASE_DIR):
        if article.id == article_id:
            return article
    return None


def list_articles() -> List[KBArticle]:
    return load_articles(kb_dir=KNOWLEDGE_BASE_DIR)


def edit_article(article_id: str, new_content: str) -> KBArticle:
    """
    Updates an existing article's content in place, keeping its original
    title/category/id. Only ever writes to the exact source_path that
    load_articles() already found on disk — never a path built fresh
    from user input — so there is no way to redirect this to write
    somewhere else.
    """
    new_content = (new_content or "").strip()
    if not new_content:
        raise ArticleValidationError("Content is required")
    if len(new_content) > MAX_ARTICLE_CONTENT_CHARS:
        raise ArticleValidationError(f"Content must be {MAX_ARTICLE_CONTENT_CHARS} characters or fewer")

    article = get_article(article_id)
    if article is None:
        raise ArticleValidationError(f"Article '{article_id}' not found")

    # Re-validate the EXISTING file's path is still within the KB dir
    # before writing — defense-in-depth even though this path came from
    # our own loader, not directly from the request.
    validated_path = _validate_within_kb_dir(article.source_path)

    _write_article_file(validated_path, article.id, article.category, article.title, new_content)

    return KBArticle(
        id=article.id,
        category=article.category,
        title=article.title,
        content=new_content,
        source_filename=article.source_filename,
        source_path=article.source_path,
    )
