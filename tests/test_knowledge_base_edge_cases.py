"""
Day 14 edge-case tests for app/knowledge_base_loader.py, using a
temporary directory (never the real 31-article knowledge_base/).

Includes a regression test for a genuine bug found during the Day 14
audit: a single corrupted/non-UTF-8 file used to crash load_articles()
entirely, silently taking every other article down with it. See the
Day 14 summary for the exact reproduction.

Run with:
    python -m tests.test_knowledge_base_edge_cases
"""

import os
import shutil
import tempfile

from app.knowledge_base_loader import load_articles

_failures = 0


def check(condition, description):
    global _failures
    status = "PASS" if condition else "FAIL"
    print(f"[{status}] {description}")
    if not condition:
        _failures += 1


def with_temp_kb(test_fn):
    tmp = tempfile.mkdtemp()
    try:
        test_fn(tmp)
    finally:
        shutil.rmtree(tmp, ignore_errors=True)


def write(tmp, relpath, content_bytes, binary=False):
    full = os.path.join(tmp, relpath)
    os.makedirs(os.path.dirname(full), exist_ok=True)
    mode = "wb" if binary else "w"
    with open(full, mode, **({} if binary else {"encoding": "utf-8"})) as f:
        f.write(content_bytes)


def test_empty_kb_directory():
    def _test(tmp):
        articles = load_articles(kb_dir=tmp)
        check(articles == [], "an empty knowledge base directory returns an empty list, not an error")

    with_temp_kb(_test)


def test_nonexistent_kb_directory():
    def _test(tmp):
        missing = os.path.join(tmp, "does-not-exist")
        articles = load_articles(kb_dir=missing)
        check(articles == [], "a nonexistent knowledge base directory returns an empty list (glob simply finds nothing), not an error")

    with_temp_kb(_test)


def test_article_with_no_frontmatter():
    def _test(tmp):
        write(tmp, "general/plain.md", "Just plain content, no frontmatter block at all.")
        articles = load_articles(kb_dir=tmp)
        check(len(articles) == 1, "a file with no frontmatter is still loaded")
        a = articles[0]
        check(a.category == "uncategorized", "missing category falls back to 'uncategorized'")
        check(a.title == "plain.md" and a.id == "plain.md", "missing title/id fall back to the filename")
        check(a.content == "Just plain content, no frontmatter block at all.", "the entire file content is preserved as the body")

    with_temp_kb(_test)


def test_article_with_partial_frontmatter():
    def _test(tmp):
        write(tmp, "billing/partial.md", '---\ntitle: "Only a title"\n---\n\nBody text here.')
        a = load_articles(kb_dir=tmp)[0]
        check(a.title == "Only a title", "a provided title is used")
        check(a.category == "uncategorized", "a missing category still falls back safely even when other frontmatter fields ARE present")
        check(a.content == "Body text here.", "body is correctly separated from the partial frontmatter block")

    with_temp_kb(_test)


def test_frontmatter_with_malformed_lines():
    def _test(tmp):
        # A line with no colon, and a value containing a colon (e.g. a URL or time) --
        # both should be handled without crashing.
        write(tmp, "general/weird.md",
              '---\nid: general-001\nthis line has no colon and should just be skipped\ntitle: "A title: with a colon"\n---\n\nBody.')
        a = load_articles(kb_dir=tmp)[0]
        check(a.id == "general-001", "well-formed lines are still parsed correctly")
        check(a.title == "A title: with a colon", "a value containing its own colon is preserved correctly (split(':', 1) semantics)")

    with_temp_kb(_test)


def test_empty_file():
    def _test(tmp):
        write(tmp, "general/empty.md", "")
        articles = load_articles(kb_dir=tmp)
        check(len(articles) == 1, "a completely empty .md file is still loaded (not skipped by the loader itself)")
        check(articles[0].content == "", "its content is correctly an empty string")

    with_temp_kb(_test)


def test_corrupted_non_utf8_file_does_not_crash_the_whole_load():
    """
    REGRESSION TEST for a genuine bug found during the Day 14 audit:
    load_articles() used to crash with an uncaught UnicodeDecodeError on
    the FIRST corrupted file it encountered, discarding every other
    valid article in the process — a single bad file meant a total
    knowledge-base outage. Fixed to skip unreadable files by default.
    """
    def _test(tmp):
        write(tmp, "account/good1.md", '---\nid: account-001\ncategory: account\ntitle: "Good One"\n---\n\nGood content.')
        write(tmp, "account/corrupted.md", b'---\nid: account-002\ncategory: account\ntitle: "Bad"\n---\n\nBroken: \xff\xfe', binary=True)
        write(tmp, "account/good2.md", '---\nid: account-003\ncategory: account\ntitle: "Good Two"\n---\n\nMore good content.')

        articles = load_articles(kb_dir=tmp)  # default: skip_unreadable=True
        ids = sorted(a.id for a in articles)
        check(ids == ["account-001", "account-003"], "both valid articles load successfully despite the corrupted file sitting between them")
        check("account-002" not in ids, "the corrupted file itself is excluded, not included with garbage content")

        raised = False
        try:
            load_articles(kb_dir=tmp, skip_unreadable=False)
        except UnicodeDecodeError:
            raised = True
        check(raised, "skip_unreadable=False still opts back into the old fail-loudly behavior when explicitly requested")

    with_temp_kb(_test)


def test_directory_matching_md_glob_pattern():
    """
    An edge case the glob pattern doesn't guard against: a directory
    literally named e.g. 'weird.md'. Opening a directory as a file
    raises IsADirectoryError (a subclass of OSError), which the Day 14
    fix's broad `except (OSError, UnicodeDecodeError)` already covers —
    this confirms that in practice, not just by class hierarchy.
    """
    def _test(tmp):
        write(tmp, "general/good.md", '---\nid: general-001\ncategory: general\ntitle: "Good"\n---\n\nContent.')
        os.makedirs(os.path.join(tmp, "general", "oddly-named.md"))  # a directory, not a file

        articles = load_articles(kb_dir=tmp)
        check(len(articles) == 1 and articles[0].id == "general-001",
              "a directory that happens to match the *.md glob pattern is safely skipped (IsADirectoryError is an OSError)")

    with_temp_kb(_test)


def test_unicode_content_preserved():
    def _test(tmp):
        write(tmp, "general/unicode.md", '---\nid: general-002\ncategory: general\ntitle: "Unicode test"\n---\n\nEmoji: 🎉 Accents: café, naïve. 中文测试.')
        a = load_articles(kb_dir=tmp)[0]
        check("🎉" in a.content and "café" in a.content and "中文测试" in a.content, "unicode content (emoji, accents, CJK) is preserved exactly, not mangled")

    with_temp_kb(_test)


def run():
    tests = [
        test_empty_kb_directory,
        test_nonexistent_kb_directory,
        test_article_with_no_frontmatter,
        test_article_with_partial_frontmatter,
        test_frontmatter_with_malformed_lines,
        test_empty_file,
        test_corrupted_non_utf8_file_does_not_crash_the_whole_load,
        test_directory_matching_md_glob_pattern,
        test_unicode_content_preserved,
    ]
    for test in tests:
        print(f"\n--- {test.__name__} ---")
        test()

    print("\n" + "-" * 60)
    if _failures == 0:
        print("ALL KNOWLEDGE BASE EDGE CASE TESTS PASSED (real filesystem, temporary directories only)")
    else:
        print(f"{_failures} TEST(S) FAILED")


if __name__ == "__main__":
    run()
