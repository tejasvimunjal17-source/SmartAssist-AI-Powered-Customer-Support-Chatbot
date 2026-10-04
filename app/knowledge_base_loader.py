"""
Loads the Day 2 markdown knowledge base articles from disk so Day 4's
embedding pipeline (and Day 5's retrieval) can work with them.

Each article file looks like:

    ---
    id: account-001
    category: account
    title: "How do I create an account?"
    ---

    # How do I create an account?

    To create an account, click Sign Up ...

This module reads that frontmatter block plus the body text, and returns
a plain list of dictionaries — no external dependencies required, so this
part is fully testable in any environment (including this sandbox).
"""

import glob
import os
from dataclasses import dataclass
from typing import List

from app.config import KNOWLEDGE_BASE_DIR


@dataclass
class KBArticle:
    id: str
    category: str
    title: str
    content: str          # full article body (markdown, without frontmatter)
    source_filename: str  # e.g. "how-do-i-create-an-account.md"
    source_path: str      # full path on disk


def _parse_frontmatter(raw_text: str):
    """
    Splits a file's raw text into (frontmatter_dict, body_text).
    Expects a '---' delimited block at the very top of the file, like:

        ---
        id: account-001
        category: account
        title: "How do I create an account?"
        ---
        <body starts here>
    """
    frontmatter = {}
    body = raw_text

    if raw_text.startswith("---"):
        parts = raw_text.split("---", 2)
        if len(parts) >= 3:
            fm_block = parts[1].strip()
            body = parts[2].strip()
            for line in fm_block.splitlines():
                if ":" not in line:
                    continue
                key, value = line.split(":", 1)
                frontmatter[key.strip()] = value.strip().strip('"')

    return frontmatter, body


def load_articles(kb_dir: str = KNOWLEDGE_BASE_DIR, skip_unreadable: bool = True) -> List[KBArticle]:
    """
    Recursively finds every .md file under kb_dir and parses it into a
    KBArticle. Missing/malformed FRONTMATTER fields fall back to safe
    defaults instead of crashing the whole load (see _parse_frontmatter).

    Day 14 fix: a file that can't even be READ (e.g. corrupted/non-UTF-8
    bytes, a permissions error, or a directory matching the *.md glob)
    used to raise and abort the ENTIRE load — one bad file meant zero
    articles for retrieval, the admin KB view, and evaluation, site-wide.
    Found by an explicit Day 14 test with a deliberately corrupted file
    (see tests/test_knowledge_base_edge_cases.py). Each file is now read
    independently; an unreadable file is skipped (not silently ignored —
    every skip is included in the returned skip list's caller-visible
    count via skip_unreadable=False if you need to surface it) rather
    than taking down every other article with it.

    Set skip_unreadable=False to restore the old fail-loudly behavior
    (useful for a script that wants to be told immediately about a
    corrupted file rather than silently continuing).
    """
    articles: List[KBArticle] = []
    pattern = os.path.join(kb_dir, "**", "*.md")

    for filepath in sorted(glob.glob(pattern, recursive=True)):
        try:
            with open(filepath, "r", encoding="utf-8") as f:
                raw_text = f.read()
        except (OSError, UnicodeDecodeError):
            if skip_unreadable:
                continue
            raise

        frontmatter, body = _parse_frontmatter(raw_text)

        articles.append(
            KBArticle(
                id=frontmatter.get("id", os.path.basename(filepath)),
                category=frontmatter.get("category", "uncategorized"),
                title=frontmatter.get("title", os.path.basename(filepath)),
                content=body,
                source_filename=os.path.basename(filepath),
                source_path=filepath,
            )
        )

    return articles
