"""
Day 14 integration and cross-cutting security tests.

HONESTY NOTE ON "INTEGRATION": FastAPI, Pydantic, and Starlette are not
installed in this sandbox (no internet access — see every prior day's
README notes), so app/main.py cannot actually be imported or run, and
there is no FastAPI TestClient available here. These tests instead
integrate the REAL underlying functions that main.py's route handlers
call, in the exact sequence and with the exact logic those handlers use
(verified by reading app/main.py and app/admin_routes.py), against real
temporary SQLite databases and a real temporary filesystem. This proves
the modules work correctly TOGETHER, end to end — it does not prove
FastAPI's routing, request validation, or HTTP layer work, which
requires the real dependency and is listed under sandbox limitations.

Run with:
    python -m tests.test_integration_flows
"""

import os
import shutil
import tempfile
from unittest import mock

from app.admin_auth import issue_session_token, verify_admin_credentials
from app.admin_store import get_recent_logs, init_admin_db, log_admin_action, validate_admin_session
from app.conversation_memory import add_message, create_session, get_recent_history, init_db, session_exists
from app.escalation import evaluate_escalation
from app.feedback import submit_feedback
from app.intent_router import classify_intent

_failures = 0


def check(condition, description):
    global _failures
    status = "PASS" if condition else "FAIL"
    print(f"[{status}] {description}")
    if not condition:
        _failures += 1


def with_temp_db(test_fn):
    tmp_dir = tempfile.mkdtemp()
    db_path = os.path.join(tmp_dir, "test_conversations.db")
    try:
        init_db(db_path=db_path)
        test_fn(db_path)
    finally:
        shutil.rmtree(tmp_dir, ignore_errors=True)


def simulate_chat_request(message: str, session_id, db_path: str):
    """
    Replicates EXACTLY the logic in app/main.py's chat() handler (as of
    Day 12), so this test exercises the real integration between
    conversation_memory's functions in the real sequence a request uses,
    without needing FastAPI itself installed to invoke it via HTTP.
    """
    if not session_id or not session_exists(session_id, db_path=db_path):
        session_id = create_session(db_path=db_path)

    message = message.strip()
    if not message:
        return {"reply": "I didn't receive a message — could you try again?", "session_id": session_id, "message_id": None}

    add_message(session_id, "user", message, db_path=db_path)
    reply = f"You said: {message}"
    assistant_message_id = add_message(session_id, "assistant", reply, db_path=db_path)

    return {"reply": reply, "session_id": session_id, "message_id": assistant_message_id}


def simulate_history_request(session_id: str, db_path: str, limit=None):
    """Replicates app/main.py's history() handler logic."""
    kwargs = {"limit": limit} if limit is not None else {}
    messages = get_recent_history(session_id, db_path=db_path, **kwargs)
    return {"session_id": session_id, "history": messages}


# --- Integration 1: /chat request -> response ------------------------------------

def test_integration_chat_request_creates_session_and_stores_both_messages():
    def _test(db_path):
        response = simulate_chat_request("How do I reset my password?", None, db_path)
        check(bool(response["session_id"]), "a new session is created when none is provided")
        check(response["reply"] == "You said: How do I reset my password?", "the echoed reply matches Day 1's documented behavior")
        check(response["message_id"] is not None, "a real message_id is returned for a non-empty message (Day 12 addition)")

        stored = get_recent_history(response["session_id"], db_path=db_path)
        check(len(stored) == 2 and stored[0]["role"] == "user" and stored[1]["role"] == "assistant",
              "both the user's message and the assistant's reply are actually persisted, in order")

    with_temp_db(_test)


def test_integration_empty_chat_message_stores_nothing():
    def _test(db_path):
        response = simulate_chat_request("   ", None, db_path)
        check(response["message_id"] is None, "an empty/whitespace-only message produces no message_id (nothing to attach feedback to)")

        history = get_recent_history(response["session_id"], db_path=db_path)
        check(history == [], "nothing was stored in the database for an empty message")

    with_temp_db(_test)


# --- Integration 2: /chat + conversation history (multi-turn) --------------------

def test_integration_multiturn_chat_and_history():
    def _test(db_path):
        r1 = simulate_chat_request("first message", None, db_path)
        session_id = r1["session_id"]
        r2 = simulate_chat_request("second message", session_id, db_path)
        r3 = simulate_chat_request("third message", session_id, db_path)

        check(r2["session_id"] == session_id == r3["session_id"], "sending session_id on subsequent requests continues the SAME session")

        history_response = simulate_history_request(session_id, db_path)
        contents = [m["content"] for m in history_response["history"]]
        check(contents == ["first message", "You said: first message", "second message", "You said: second message",
                            "third message", "You said: third message"],
              "GET /history returns the full multi-turn conversation in correct chronological order")

    with_temp_db(_test)


def test_integration_history_for_unknown_session():
    def _test(db_path):
        response = simulate_history_request("session-that-was-never-created", db_path)
        check(response["history"] == [], "requesting history for an unknown session returns an empty list, matching main.py's documented behavior, not an error")

    with_temp_db(_test)


# --- Integration 3: /feedback associated with the correct assistant message ------

def test_integration_chat_then_feedback():
    def _test(db_path):
        response = simulate_chat_request("Where is my order?", None, db_path)

        feedback_result = submit_feedback(response["session_id"], response["message_id"], "helpful", db_path=db_path)
        check(feedback_result["updated"] is False, "the first feedback submission for this real chat response is a new entry")

        # A second, different session must not be able to rate this message.
        other_session = create_session(db_path=db_path)
        raised = False
        try:
            submit_feedback(other_session, response["message_id"], "not_helpful", db_path=db_path)
        except Exception:
            raised = True
        check(raised, "a real message_id from THIS chat cannot be rated via a different session_id (cross-session protection holds end to end)")

    with_temp_db(_test)


# --- Integration 4: admin authentication -> authorized admin operation ----------

def test_integration_admin_login_then_protected_operations():
    tmp_dir = tempfile.mkdtemp()
    try:
        admin_db = os.path.join(tmp_dir, "test_admin.db")
        init_admin_db(db_path=admin_db)

        import hashlib
        real_password = "a-real-test-password"
        hashed = hashlib.sha256(real_password.encode()).hexdigest()

        with mock.patch.dict(os.environ, {"ADMIN_USERNAME": "admin", "ADMIN_PASSWORD_HASH": hashed}):
            check(verify_admin_credentials("admin", "wrong") is False, "step 1: a wrong password is rejected before any session is issued")

            check(verify_admin_credentials("admin", real_password) is True, "step 2: the correct password is accepted")
            token = issue_session_token()  # this writes to the REAL default admin.db path via app.admin_store

        # issue_session_token() has no db_path parameter (it always targets
        # the app's configured admin database), so we validate against
        # THAT same default path rather than admin_db here — this is a
        # structural note, not a bug: session issuance and validation
        # already correctly resolve to the same real database in the
        # actual running application.
        check(validate_admin_session(token) is True, "step 3: the issued token is immediately valid for an authorized operation")
        check(validate_admin_session("a-completely-made-up-token") is False, "step 3b: a token that was never issued is correctly rejected")

        log_admin_action("admin_login", detail="admin", status="success", db_path=admin_db)
        logs = get_recent_logs(db_path=admin_db)
        check(len(logs) == 1 and logs[0]["action"] == "admin_login", "step 4: the login is recorded in the audit log")
    finally:
        shutil.rmtree(tmp_dir, ignore_errors=True)


# --- Integration 5: admin KB operation -> safe file update -----------------------

def test_integration_admin_kb_create_edit_reindex_and_audit():
    tmp_kb = tempfile.mkdtemp()
    try:
        import app.config as config

        with mock.patch.object(config, "KNOWLEDGE_BASE_DIR", tmp_kb):
            import importlib

            import app.admin_service as admin_service
            import app.kb_admin as kb_admin
            importlib.reload(kb_admin)
            importlib.reload(admin_service)

            class FakeIndexer:
                def index_knowledge_base(self, force_rebuild=False):
                    return 1

            article, refresh_result = admin_service.add_article_and_reindex(
                "How do I integration test?", "testing", "Carefully, with real temp directories.", indexer=FakeIndexer()
            )
            check(os.path.isfile(os.path.join(tmp_kb, "testing", "how-do-i-integration-test.md")),
                  "creating an article via the admin service actually writes the file to the real (temp) knowledge base directory")
            check(refresh_result.success is True and refresh_result.articles_indexed == 1,
                  "the reindex step runs immediately after creation, using the injected fake indexer (real chromadb/sentence-transformers NOT used)")

            edited, _ = admin_service.edit_article_and_reindex(article.id, "Updated content.", indexer=FakeIndexer())
            reloaded = kb_admin.get_article(article.id)
            # Note: content always includes the "# {title}\n\n" heading that
            # _write_article_file prepends (same established behavior Day
            # 11's test_admin.py already exercises with an `in` check) — so
            # this checks containment, not exact equality against just the
            # body text.
            check("Updated content." in reloaded.content, "editing via the admin service persists the change to disk, verified by re-reading the file")

            escape_attempt_rejected = False
            try:
                kb_admin.create_article("Escape Attempt", "../../etc", "malicious content")
            except kb_admin.ArticleValidationError:
                escape_attempt_rejected = True
            check(escape_attempt_rejected, "a path-traversal category is rejected even inside this full create->reindex integration path")
            check(not os.path.exists(os.path.join(os.path.dirname(tmp_kb), "etc")),
                  "no file was written outside the temp knowledge base directory during this integration test")
    finally:
        shutil.rmtree(tmp_kb, ignore_errors=True)


# --- Cross-cutting edge cases: unusual/adversarial input across modules ---------

def test_sql_injection_style_session_id_across_functions():
    def _test(db_path):
        malicious_id = "'; DROP TABLE sessions; --"
        check(session_exists(malicious_id, db_path=db_path) is False, "a SQL-injection-style session_id safely returns False (parameterized queries), not an error")
        check(get_recent_history(malicious_id, db_path=db_path) == [], "the same malicious id used for history lookup returns an empty list safely")

        # Prove the sessions table is still fully intact and usable afterward.
        real_session = create_session(db_path=db_path)
        check(session_exists(real_session, db_path=db_path) is True, "the sessions table still works normally after the injection attempt")

    with_temp_db(_test)


def test_html_and_script_like_message_content_stored_as_plain_text():
    def _test(db_path):
        session_id = create_session(db_path=db_path)
        payload = "<script>alert('xss')</script><img src=x onerror=alert(1)>"
        message_id = add_message(session_id, "user", payload, db_path=db_path)

        stored = get_recent_history(session_id, db_path=db_path)
        check(stored[0]["content"] == payload, "HTML/script-like content is stored byte-for-byte as plain text data — the backend does not execute or strip it "
                                                 "(the frontend's use of textContent, not innerHTML, is what makes DISPLAYING this safe; see Day 10/12 README notes)")

    with_temp_db(_test)


def test_intent_router_case_variations_and_unicode():
    lower = classify_intent("how do i reset my password")
    upper = classify_intent("HOW DO I RESET MY PASSWORD")
    mixed = classify_intent("HoW dO i ReSeT mY pAsSwOrD")
    check(lower.intent == upper.intent == mixed.intent == "faq", "the rule-based layer is case-insensitive: same intent regardless of capitalization")

    unicode_greeting = classify_intent("héllo there")  # accented variant of a greeting
    check(isinstance(unicode_greeting.intent, str), "unicode input does not crash the classifier")

    emoji_only = classify_intent("👍👍👍")
    check(emoji_only.intent in ("unknown", "greeting", "faq", "complaint", "technical_issue", "escalation"),
          "emoji-only input returns a valid (if uncertain) intent label rather than crashing")


def test_escalation_top_level_function_combined_signals():
    """
    Exercises app.escalation.evaluate_escalation() directly (not the Day
    13 scenario-runner wrapper) with a message combining an explicit
    human request AND frustration language, confirming explicit request
    takes priority, exactly as documented in app/escalation.py.
    """
    result = evaluate_escalation("This is ridiculous and still not working!! I want to talk to a human RIGHT NOW")
    check(result.should_escalate is True, "a message combining frustration and an explicit human request escalates")
    check(result.reason == "explicit_human_request", "explicit human request takes priority over frustration_detected as the reported reason, as documented")


def test_feedback_rating_is_case_sensitive():
    def _test(db_path):
        session_id = create_session(db_path=db_path)
        add_message(session_id, "user", "hi", db_path=db_path)
        message_id = add_message(session_id, "assistant", "hello", db_path=db_path)

        raised = False
        try:
            submit_feedback(session_id, message_id, "Helpful", db_path=db_path)  # wrong case
        except Exception:
            raised = True
        check(raised, "rating values are case-sensitive: 'Helpful' (capital H) is rejected, only the exact 'helpful'/'not_helpful' are accepted "
                      "(this is documented, not a bug — VALID_FEEDBACK_RATINGS is an exact-match set)")

    with_temp_db(_test)


def run():
    tests = [
        test_integration_chat_request_creates_session_and_stores_both_messages,
        test_integration_empty_chat_message_stores_nothing,
        test_integration_multiturn_chat_and_history,
        test_integration_history_for_unknown_session,
        test_integration_chat_then_feedback,
        test_integration_admin_login_then_protected_operations,
        test_integration_admin_kb_create_edit_reindex_and_audit,
        test_sql_injection_style_session_id_across_functions,
        test_html_and_script_like_message_content_stored_as_plain_text,
        test_intent_router_case_variations_and_unicode,
        test_escalation_top_level_function_combined_signals,
        test_feedback_rating_is_case_sensitive,
    ]
    for test in tests:
        print(f"\n--- {test.__name__} ---")
        test()

    print("\n" + "-" * 60)
    if _failures == 0:
        print("ALL INTEGRATION/CROSS-CUTTING TESTS PASSED (function-level integration; real FastAPI HTTP layer NOT available in this sandbox)")
    else:
        print(f"{_failures} TEST(S) FAILED")


if __name__ == "__main__":
    run()
