"""
Day 12 tests. Feedback storage uses sqlite3 (Python standard library),
so — like Day 8 — this entire suite runs against a REAL temporary
SQLite database. No mocking needed.

Run with:
    python -m tests.test_feedback
"""

import os
import shutil
import tempfile

from app.conversation_memory import add_message, create_session, init_db
from app.feedback import FeedbackValidationError, get_recent_feedback, submit_feedback

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


def _make_session_with_assistant_message(db_path):
    session_id = create_session(db_path=db_path)
    add_message(session_id, "user", "How do I reset my password?", db_path=db_path)
    assistant_message_id = add_message(
        session_id, "assistant", "Go to settings and click reset.", db_path=db_path
    )
    return session_id, assistant_message_id


def test_helpful_feedback_is_stored():
    def _test(db_path):
        session_id, message_id = _make_session_with_assistant_message(db_path)
        result = submit_feedback(session_id, message_id, "helpful", db_path=db_path)

        check(result["rating"] == "helpful", "helpful feedback is stored with the correct rating")
        check(result["updated"] is False, "a first-time submission is reported as NOT an update")

        stored = get_recent_feedback(db_path=db_path)
        check(len(stored) == 1 and stored[0]["rating"] == "helpful", "feedback is actually persisted and retrievable")

    with_temp_db(_test)


def test_not_helpful_feedback_is_stored():
    def _test(db_path):
        session_id, message_id = _make_session_with_assistant_message(db_path)
        result = submit_feedback(session_id, message_id, "not_helpful", comment="Didn't solve my issue", db_path=db_path)

        check(result["rating"] == "not_helpful", "not-helpful feedback is stored with the correct rating")
        check(result["comment"] == "Didn't solve my issue", "an optional comment is stored correctly")

    with_temp_db(_test)


def test_feedback_associated_with_correct_session_and_message():
    def _test(db_path):
        session_id, message_id = _make_session_with_assistant_message(db_path)
        result = submit_feedback(session_id, message_id, "helpful", db_path=db_path)

        check(result["session_id"] == session_id, "feedback is linked to the correct session_id")
        check(result["message_id"] == message_id, "feedback is linked to the correct message_id")

    with_temp_db(_test)


def test_invalid_rating_rejected():
    def _test(db_path):
        session_id, message_id = _make_session_with_assistant_message(db_path)
        raised = False
        try:
            submit_feedback(session_id, message_id, "super-duper-helpful", db_path=db_path)
        except FeedbackValidationError:
            raised = True
        check(raised, "an invalid rating value is rejected")
        check(len(get_recent_feedback(db_path=db_path)) == 0, "the invalid-rating submission was NOT stored")

    with_temp_db(_test)


def test_nonexistent_session_handled():
    def _test(db_path):
        raised = False
        try:
            submit_feedback("session-does-not-exist", 1, "helpful", db_path=db_path)
        except FeedbackValidationError:
            raised = True
        check(raised, "feedback against a nonexistent session is rejected")

    with_temp_db(_test)


def test_nonexistent_message_handled():
    def _test(db_path):
        session_id = create_session(db_path=db_path)
        raised = False
        try:
            submit_feedback(session_id, 99999, "helpful", db_path=db_path)
        except FeedbackValidationError:
            raised = True
        check(raised, "feedback against a nonexistent message_id is rejected")

    with_temp_db(_test)


def test_feedback_on_user_message_rejected():
    """Feedback should only be attachable to assistant responses, not the customer's own message."""
    def _test(db_path):
        session_id = create_session(db_path=db_path)
        user_message_id = add_message(session_id, "user", "hello", db_path=db_path)

        raised = False
        try:
            submit_feedback(session_id, user_message_id, "helpful", db_path=db_path)
        except FeedbackValidationError:
            raised = True
        check(raised, "feedback against a USER message (not assistant) is rejected")

    with_temp_db(_test)


def test_cross_session_message_id_rejected():
    """A message_id that exists, but belongs to a DIFFERENT session, must not be usable via another session_id."""
    def _test(db_path):
        session_a, message_id_a = _make_session_with_assistant_message(db_path)
        session_b = create_session(db_path=db_path)

        raised = False
        try:
            submit_feedback(session_b, message_id_a, "helpful", db_path=db_path)
        except FeedbackValidationError:
            raised = True
        check(raised, "a real message_id from a DIFFERENT session cannot be used to attach feedback via session_b")

    with_temp_db(_test)


def test_duplicate_feedback_updates_instead_of_duplicating():
    def _test(db_path):
        session_id, message_id = _make_session_with_assistant_message(db_path)

        first = submit_feedback(session_id, message_id, "helpful", db_path=db_path)
        second = submit_feedback(session_id, message_id, "not_helpful", comment="changed my mind", db_path=db_path)

        check(first["updated"] is False, "the first submission is not marked as an update")
        check(second["updated"] is True, "re-rating the same message IS marked as an update")
        check(first["id"] == second["id"], "re-rating reuses the same feedback row id (no duplicate row)")

        all_feedback = get_recent_feedback(db_path=db_path)
        check(len(all_feedback) == 1, "only ONE feedback row exists after two submissions for the same message")
        check(all_feedback[0]["rating"] == "not_helpful", "the stored rating reflects the latest submission")

    with_temp_db(_test)


def test_empty_and_long_comments():
    def _test(db_path):
        session_id, message_id = _make_session_with_assistant_message(db_path)

        result = submit_feedback(session_id, message_id, "helpful", comment="   ", db_path=db_path)
        check(result["comment"] is None, "a whitespace-only comment is normalized to None, not stored as blank text")

        session_id2, message_id2 = _make_session_with_assistant_message(db_path)
        too_long = "x" * 5000
        raised = False
        try:
            submit_feedback(session_id2, message_id2, "helpful", comment=too_long, db_path=db_path)
        except FeedbackValidationError:
            raised = True
        check(raised, "an excessively long comment (5000 chars) is rejected")

    with_temp_db(_test)


def test_sql_injection_style_comment_stored_safely():
    def _test(db_path):
        session_id, message_id = _make_session_with_assistant_message(db_path)
        malicious = "'); DROP TABLE feedback; --"
        result = submit_feedback(session_id, message_id, "not_helpful", comment=malicious, db_path=db_path)

        check(result["comment"] == malicious, "SQL-injection-style comment text is stored as plain data (parameterized queries)")

        # Prove the feedback table still exists and works normally afterward.
        session_id2, message_id2 = _make_session_with_assistant_message(db_path)
        submit_feedback(session_id2, message_id2, "helpful", db_path=db_path)
        check(len(get_recent_feedback(db_path=db_path)) == 2, "feedback table still intact and functional after injection-style content")

    with_temp_db(_test)


def test_session_isolation():
    def _test(db_path):
        session_a, message_id_a = _make_session_with_assistant_message(db_path)
        session_b, message_id_b = _make_session_with_assistant_message(db_path)

        submit_feedback(session_a, message_id_a, "helpful", db_path=db_path)
        submit_feedback(session_b, message_id_b, "not_helpful", db_path=db_path)

        all_feedback = get_recent_feedback(db_path=db_path)
        check(len(all_feedback) == 2, "both sessions' feedback is stored independently")

        session_ids_seen = {entry["session_id"] for entry in all_feedback}
        check(session_ids_seen == {session_a, session_b}, "feedback entries correctly retain their own distinct session_id")

    with_temp_db(_test)


def test_malformed_message_id_type_handled():
    """A message_id that is a string instead of an int shouldn't crash the lookup."""
    def _test(db_path):
        session_id, _ = _make_session_with_assistant_message(db_path)
        raised = False
        try:
            submit_feedback(session_id, "not-a-number", "helpful", db_path=db_path)
        except (FeedbackValidationError, TypeError, ValueError):
            raised = True
        check(raised, "a malformed (non-integer) message_id does not crash the system uncontrolled — it's rejected")

    with_temp_db(_test)


def run():
    tests = [
        test_helpful_feedback_is_stored,
        test_not_helpful_feedback_is_stored,
        test_feedback_associated_with_correct_session_and_message,
        test_invalid_rating_rejected,
        test_nonexistent_session_handled,
        test_nonexistent_message_handled,
        test_feedback_on_user_message_rejected,
        test_cross_session_message_id_rejected,
        test_duplicate_feedback_updates_instead_of_duplicating,
        test_empty_and_long_comments,
        test_sql_injection_style_comment_stored_safely,
        test_session_isolation,
        test_malformed_message_id_type_handled,
    ]

    for test in tests:
        print(f"\n--- {test.__name__} ---")
        test()

    print("\n" + "-" * 60)
    if _failures == 0:
        print("ALL FEEDBACK TESTS PASSED (real SQLite, temporary database files)")
    else:
        print(f"{_failures} TEST(S) FAILED")


if __name__ == "__main__":
    run()
