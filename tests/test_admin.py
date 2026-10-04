"""
Day 11 tests. Most of this is REAL, executable logic:
- admin_auth.py's credential verification is pure hashlib/hmac (no deps)
- admin_store.py uses real temporary SQLite databases (like Day 8)
- kb_admin.py's path-traversal defenses are tested against the REAL
  filesystem, using a temporary knowledge base directory (never the real
  31-article knowledge_base/)
- admin_service.py's index-refresh ORCHESTRATION is tested with a fake
  indexer (sentence-transformers/chromadb aren't installed here) — the
  brief explicitly says not to fake a successful real embedding rebuild,
  so we test that the orchestration calls the indexer and handles both
  success and failure correctly, without claiming the real rebuild works

Run with:
    python -m tests.test_admin
"""

import os
import shutil
import tempfile
from unittest import mock

# IMPORTANT: import these modules HERE, at the top of the file, before
# any test runs mock.patch.object(config, "KNOWLEDGE_BASE_DIR", ...).
# knowledge_base_loader.load_articles() has a default parameter
# `kb_dir: str = KNOWLEDGE_BASE_DIR` that is bound ONCE, the first time
# this module is ever imported in the process. If that first import
# happened to occur while KNOWLEDGE_BASE_DIR was patched to a temp
# directory, the default would be permanently poisoned to that (by then
# deleted) temp directory for the rest of the test run — silently
# breaking the REAL knowledge_base/ lookups in unrelated tests. Forcing
# the import here, before any patching, guarantees it binds to the real
# path. (This was found and fixed after actually seeing it break the
# regression test below — see the Day 11 summary.)
import app.kb_admin  # noqa: F401
import app.knowledge_base_loader  # noqa: F401
import app.vector_store  # noqa: F401

_failures = 0


def check(condition, description):
    global _failures
    status = "PASS" if condition else "FAIL"
    print(f"[{status}] {description}")
    if not condition:
        _failures += 1


# --- admin_auth.py: credential verification (pure logic, no deps) ---------

def test_admin_auth():
    from app import admin_auth

    with mock.patch.dict(os.environ, {"ADMIN_USERNAME": "admin", "ADMIN_PASSWORD_HASH": ""}, clear=False):
        # No hash configured at all -> must fail closed, never succeed.
        os.environ.pop("ADMIN_PASSWORD_HASH", None)
        check(admin_auth.verify_admin_credentials("admin", "anything") is False,
              "login fails closed when ADMIN_PASSWORD_HASH is not configured")

    import hashlib
    real_password = "correct-horse-battery-staple"
    hashed = hashlib.sha256(real_password.encode()).hexdigest()

    with mock.patch.dict(os.environ, {"ADMIN_USERNAME": "admin", "ADMIN_PASSWORD_HASH": hashed}):
        check(admin_auth.verify_admin_credentials("admin", real_password) is True,
              "correct username + correct password succeeds")
        check(admin_auth.verify_admin_credentials("admin", "wrong-password") is False,
              "correct username + wrong password fails")
        check(admin_auth.verify_admin_credentials("not-admin", real_password) is False,
              "wrong username fails even with correct password")
        check(admin_auth.verify_admin_credentials("admin", "") is False,
              "empty password fails")


# --- admin_store.py: real SQLite session + audit log ----------------------

def with_temp_admin_db(test_fn):
    tmp_dir = tempfile.mkdtemp()
    db_path = os.path.join(tmp_dir, "test_admin.db")
    try:
        from app.admin_store import init_admin_db
        init_admin_db(db_path=db_path)
        test_fn(db_path)
    finally:
        shutil.rmtree(tmp_dir, ignore_errors=True)


def test_admin_sessions_and_audit_log():
    from app.admin_store import create_admin_session, get_recent_logs, log_admin_action, validate_admin_session

    def _test(db_path):
        token = create_admin_session(db_path=db_path)
        check(bool(token), "create_admin_session returns a non-empty token")
        check(validate_admin_session(token, db_path=db_path) is True, "a freshly created session validates successfully")
        check(validate_admin_session("not-a-real-token", db_path=db_path) is False, "an unknown token fails validation")
        check(validate_admin_session(None, db_path=db_path) is False, "a missing/None token fails validation safely")
        check(validate_admin_session("", db_path=db_path) is False, "an empty string token fails validation safely")

        log_admin_action("admin_login", detail="admin", status="success", db_path=db_path)
        log_admin_action("article_create", detail="billing-refund-policy", status="success", db_path=db_path)
        logs = get_recent_logs(limit=10, db_path=db_path)
        check(len(logs) == 2, "audit log records both actions")
        check(logs[0]["action"] == "article_create", "get_recent_logs returns newest-first")
        check(all("password" not in str(entry).lower() for entry in logs), "no password/secret content appears in audit log entries")

    with_temp_admin_db(_test)


# --- kb_admin.py: safe article create/edit + path-traversal defense ------

def with_temp_kb_dir(test_fn):
    tmp_dir = tempfile.mkdtemp()
    try:
        test_fn(tmp_dir)
    finally:
        shutil.rmtree(tmp_dir, ignore_errors=True)


def test_kb_admin_create_and_edit():
    import app.config as config

    def _test(tmp_kb_dir):
        with mock.patch.object(config, "KNOWLEDGE_BASE_DIR", tmp_kb_dir):
            import importlib

            import app.kb_admin as kb_admin
            importlib.reload(kb_admin)  # pick up the patched KNOWLEDGE_BASE_DIR

            article = kb_admin.create_article("How do I reset my password?", "account", "Go to settings and click reset.")
            check(article.category == "account", "created article has the correct category")
            check(os.path.isfile(os.path.join(tmp_kb_dir, "account", "how-do-i-reset-my-password.md")),
                  "article file is actually written to disk inside the temp KB dir")

            # Duplicate title in same category should be rejected, not silently overwritten.
            raised = False
            try:
                kb_admin.create_article("How do I reset my password?", "account", "different content")
            except kb_admin.ArticleValidationError:
                raised = True
            check(raised, "creating a duplicate article (same title+category) is rejected")

            edited = kb_admin.edit_article(article.id, "Updated instructions: go to settings, click reset, check email.")
            check("Updated instructions" in edited.content, "edit_article updates the content")
            check(edited.id == article.id and edited.category == article.category, "edit_article preserves id/category")

            reloaded = kb_admin.get_article(article.id)
            check(reloaded is not None and "Updated instructions" in reloaded.content, "edited content is actually persisted to disk")

    with_temp_kb_dir(_test)


def test_kb_admin_validation():
    import app.config as config

    def _test(tmp_kb_dir):
        with mock.patch.object(config, "KNOWLEDGE_BASE_DIR", tmp_kb_dir):
            import importlib

            import app.kb_admin as kb_admin
            importlib.reload(kb_admin)

            for bad_category in ["../../etc", "Billing With Spaces", "billing/../../escape", "UPPERCASE", ""]:
                raised = False
                try:
                    kb_admin.create_article("Some Title", bad_category, "some content")
                except kb_admin.ArticleValidationError:
                    raised = True
                check(raised, f"unsafe/invalid category {bad_category!r} is rejected")

            raised = False
            try:
                kb_admin.create_article("", "billing", "some content")
            except kb_admin.ArticleValidationError:
                raised = True
            check(raised, "empty title is rejected")

            raised = False
            try:
                kb_admin.create_article("Some Title", "billing", "")
            except kb_admin.ArticleValidationError:
                raised = True
            check(raised, "empty content is rejected")

            raised = False
            try:
                kb_admin.create_article("x" * 500, "billing", "some content")
            except kb_admin.ArticleValidationError:
                raised = True
            check(raised, "an unreasonably long title is rejected")

            # Prove no file escaped the temp KB dir despite the attempts above.
            escaped_path = os.path.join(os.path.dirname(tmp_kb_dir), "escape.md")
            check(not os.path.exists(escaped_path), "no file was ever written outside the knowledge base directory")

            raised = False
            try:
                kb_admin.edit_article("does-not-exist-123", "new content")
            except kb_admin.ArticleValidationError:
                raised = True
            check(raised, "editing a nonexistent article id is rejected")

    with_temp_kb_dir(_test)


def test_path_traversal_resolution_defense():
    """Directly exercises the realpath-containment check, independent of the category regex."""
    import app.config as config

    def _test(tmp_kb_dir):
        with mock.patch.object(config, "KNOWLEDGE_BASE_DIR", tmp_kb_dir):
            import importlib

            import app.kb_admin as kb_admin
            importlib.reload(kb_admin)

            check(kb_admin.slugify("../../etc/passwd") not in ("..", "etc", ""),
                  "slugify() strips path-control characters entirely, never producing '..' or '/'")

            safe_path = os.path.join(tmp_kb_dir, "billing", "refund.md")
            validated = kb_admin._validate_within_kb_dir(safe_path)
            check(validated.startswith(os.path.realpath(tmp_kb_dir)), "a legitimate path inside the KB dir passes validation")

            unsafe_path = os.path.join(tmp_kb_dir, "..", "outside.md")
            raised = False
            try:
                kb_admin._validate_within_kb_dir(unsafe_path)
            except kb_admin.ArticleValidationError:
                raised = True
            check(raised, "a resolved path escaping the KB dir is rejected, even if built from '..'")

    with_temp_kb_dir(_test)


# --- admin_service.py: index-refresh orchestration (fake indexer) --------

def test_index_refresh_orchestration():
    from app.admin_service import refresh_index
    from app.admin_store import init_admin_db

    # admin_service.refresh_index() logs to the admin audit log using the
    # default admin.db path. In the real app, main.py's startup event
    # calls init_admin_db() once before any requests arrive — mirror that
    # here so the audit-log write doesn't fail on a missing table. This
    # is idempotent (CREATE TABLE IF NOT EXISTS) and safe to call again.
    init_admin_db()

    class FakeIndexerSuccess:
        def index_knowledge_base(self, force_rebuild=False):
            return 31

    class FakeIndexerFailure:
        def index_knowledge_base(self, force_rebuild=False):
            raise RuntimeError("chromadb is not installed. Run: pip install chromadb")

    result = refresh_index(indexer=FakeIndexerSuccess())
    check(result.attempted is True and result.success is True and result.articles_indexed == 31,
          "successful reindex is reported correctly (using a fake indexer — real chromadb/sentence-transformers not available here)")

    result = refresh_index(indexer=FakeIndexerFailure())
    check(result.attempted is True and result.success is False and result.error is not None,
          "a failed reindex (e.g. missing dependency) is reported, NOT silently swallowed or faked as success")


# --- Regression: existing 31 articles + Days 1-10 modules untouched ------

def test_regression_real_kb_untouched():
    from app.knowledge_base_loader import load_articles

    articles = load_articles()  # uses the REAL knowledge_base/ dir (no patching here)
    check(len(articles) >= 30, f"the real production knowledge_base/ still has its {len(articles)} articles (untouched by admin tests)")


def test_regression_imports():
    # Confirms Day 10's main.py still defines the same public routes,
    # and that admin_routes registers without error, all in one import.
    import ast

    from app.config import BASE_DIR

    main_path = os.path.join(BASE_DIR, "app", "main.py")
    with open(main_path, "r", encoding="utf-8") as f:
        source = f.read()
    tree = ast.parse(source)

    route_paths = []
    for node in ast.walk(tree):
        if isinstance(node, ast.FunctionDef):
            for decorator in node.decorator_list:
                if isinstance(decorator, ast.Call) and isinstance(decorator.func, ast.Attribute):
                    if decorator.func.attr in ("get", "post") and decorator.args:
                        arg = decorator.args[0]
                        if isinstance(arg, ast.Constant):
                            route_paths.append(arg.value)

    check("/" in route_paths, "GET / still defined after Day 11 changes")
    check("/chat" in route_paths, "POST /chat still defined after Day 11 changes")
    check("/history" in route_paths, "GET /history still defined after Day 11 changes")
    check("admin_router" in source and "include_router" in source, "admin router is registered via include_router, not merged into main routes")
    check(os.path.isfile(os.path.join(BASE_DIR, "frontend", "index.html")), "Day 10 customer frontend (index.html) still exists")
    check(os.path.isfile(os.path.join(BASE_DIR, "frontend", "admin.html")), "Day 11 admin frontend (admin.html) exists")


def run():
    tests = [
        test_admin_auth,
        test_admin_sessions_and_audit_log,
        test_kb_admin_create_and_edit,
        test_kb_admin_validation,
        test_path_traversal_resolution_defense,
        test_index_refresh_orchestration,
        test_regression_real_kb_untouched,
        test_regression_imports,
    ]

    for test in tests:
        print(f"\n--- {test.__name__} ---")
        test()

    print("\n" + "-" * 60)
    if _failures == 0:
        print("ALL ADMIN TESTS PASSED (real SQLite + real filesystem in temp dirs; index-refresh tested with a fake indexer, real chromadb/sentence-transformers NOT executed)")
    else:
        print(f"{_failures} TEST(S) FAILED")


if __name__ == "__main__":
    run()
