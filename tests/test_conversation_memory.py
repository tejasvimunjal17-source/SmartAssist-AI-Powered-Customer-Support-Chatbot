"""
Tests app/conversation_memory.py and app/conversation_context.py against
a REAL temporary SQLite database file (sqlite3 is Python's standard
library — no external dependency, no internet needed — so every test
here runs for real, unlike the sentence-transformers/chromadb/Gemini
tests in earlier days).

Each test function gets its OWN temporary database file (via
tempfile.mkdtemp) so tests can't interfere with each other or with your
real conversations.db.

Run with:
    python -m tests.test_conversation_memory
"""

import os
import shutil
import tempfile

from app.conversation_context import build_conversation_context
from app.conversation_memory import (
    add_message,
    count_messages,
    create_session,
    get_recent_history,
    init_db,
    session_exists,
)

_failures = 0


def check(condition, description):
    global _failures
    status = "PASS" if condition else "FAIL"
    print(f"[{status}] {description}")
    if not condition:
        _failures += 1


def with_temp_db(test_fn):
    """Runs test_fn(db_path) against a fresh temp SQLite file, then cleans up."""
    tmp_dir = tempfile.mkdtemp()
    db_path = os.path.join(tmp_dir, "test_conversations.db")
    try:
        init_db(db_path=db_path)
        test_fn(db_path)
    finally:
        shutil.rmtree(tmp_dir, ignore_errors=True)


def test_database_initialization():
    def _test(db_path):
        check(os.path.exists(db_path), "database file created by init_db()")
        # Calling init_db() again should not error (CREATE TABLE IF NOT EXISTS)
        init_db(db_path=db_path)
        check(True, "init_db() can be called twice without error")

    with_temp_db(_test)


def test_session_creation_and_uniqueness():
    def _test(db_path):
        s1 = create_session(db_path=db_path)
        s2 = create_session(db_path=db_path)
        check(bool(s1) and bool(s2), "create_session() returns a non-empty id")
        check(s1 != s2, "two created sessions have unique ids")
        check(session_exists(s1, db_path=db_path), "created session is found by session_exists()")

    with_temp_db(_test)


def test_unknown_session_handling():
    def _test(db_path):
        check(session_exists("does-not-exist", db_path=db_path) is False, "session_exists() is False for an unknown id")
        check(get_recent_history("does-not-exist", db_path=db_path) == [], "get_recent_history() returns [] for an unknown session")
        check(build_conversation_context("does-not-exist", db_path=db_path) == [], "build_conversation_context() returns [] for an unknown session")

        raised = False
        try:
            add_message("does-not-exist", "user", "hello", db_path=db_path)
        except ValueError:
            raised = True
        check(raised, "add_message() raises ValueError for a nonexistent session (doesn't silently succeed)")

    with_temp_db(_test)


def test_storing_and_retrieving_messages():
    def _test(db_path):
        session_id = create_session(db_path=db_path)
        add_message(session_id, "user", "Hello, I need help", db_path=db_path)
        add_message(session_id, "assistant", "Sure, how can I help?", db_path=db_path)

        history = get_recent_history(session_id, db_path=db_path)
        check(len(history) == 2, "both stored messages are retrieved")
        check(history[0]["role"] == "user" and history[0]["content"] == "Hello, I need help", "first message stored/retrieved correctly")
        check(history[1]["role"] == "assistant" and history[1]["content"] == "Sure, how can I help?", "second message stored/retrieved correctly")
        check(history[0]["timestamp"] < history[1]["timestamp"] or history[0]["timestamp"] <= history[1]["timestamp"], "messages have timestamps")

    with_temp_db(_test)


def test_chronological_ordering():
    def _test(db_path):
        session_id = create_session(db_path=db_path)
        for i in range(5):
            add_message(session_id, "user" if i % 2 == 0 else "assistant", f"message {i}", db_path=db_path)

        history = get_recent_history(session_id, limit=10, db_path=db_path)
        contents = [m["content"] for m in history]
        check(contents == [f"message {i}" for i in range(5)], "messages returned in chronological (oldest-first) order")

    with_temp_db(_test)


def test_sliding_window_limit():
    def _test(db_path):
        session_id = create_session(db_path=db_path)
        for i in range(50):
            add_message(session_id, "user", f"message {i}", db_path=db_path)

        recent = get_recent_history(session_id, limit=10, db_path=db_path)
        check(len(recent) == 10, "sliding window returns exactly the configured limit (10) out of 50 stored")
        check(recent[-1]["content"] == "message 49", "sliding window returns the MOST RECENT messages, not the oldest")
        check(recent[0]["content"] == "message 40", "sliding window's oldest entry is correctly the 40th message (49-10+1..49)")

        total_stored = count_messages(session_id, db_path=db_path)
        check(total_stored == 50, "all 50 messages remain persisted in SQLite — sliding window did not delete anything")

    with_temp_db(_test)


def test_session_isolation():
    def _test(db_path):
        session_a = create_session(db_path=db_path)
        session_b = create_session(db_path=db_path)

        add_message(session_a, "user", "message for A", db_path=db_path)
        add_message(session_b, "user", "message for B", db_path=db_path)

        history_a = get_recent_history(session_a, db_path=db_path)
        history_b = get_recent_history(session_b, db_path=db_path)

        check(len(history_a) == 1 and history_a[0]["content"] == "message for A", "session A only sees its own message")
        check(len(history_b) == 1 and history_b[0]["content"] == "message for B", "session B only sees its own message")
        check("message for B" not in [m["content"] for m in history_a], "session A cannot see session B's messages (no cross-session leakage)")

    with_temp_db(_test)


def test_invalid_role_rejected():
    def _test(db_path):
        session_id = create_session(db_path=db_path)
        raised = False
        try:
            add_message(session_id, "system_hacker", "malicious role", db_path=db_path)
        except ValueError:
            raised = True
        check(raised, "an invalid role ('system_hacker') is rejected with ValueError")
        check(count_messages(session_id, db_path=db_path) == 0, "the invalid-role message was NOT stored")

    with_temp_db(_test)


def test_empty_and_whitespace_message_rejected():
    def _test(db_path):
        session_id = create_session(db_path=db_path)
        for bad_content in ["", "   ", "\n\t"]:
            raised = False
            try:
                add_message(session_id, "user", bad_content, db_path=db_path)
            except ValueError:
                raised = True
            check(raised, f"empty/whitespace content {bad_content!r} is rejected with ValueError")
        check(count_messages(session_id, db_path=db_path) == 0, "no empty messages were stored")

    with_temp_db(_test)


def test_extremely_long_message_handled():
    def _test(db_path):
        session_id = create_session(db_path=db_path)
        long_message = "x" * 20000
        add_message(session_id, "user", long_message, db_path=db_path)
        history = get_recent_history(session_id, db_path=db_path)
        check(len(history) == 1 and history[0]["content"] == long_message, "a very long message is stored and retrieved intact")

    with_temp_db(_test)


def test_sql_injection_style_content_is_stored_safely():
    def _test(db_path):
        session_id = create_session(db_path=db_path)
        malicious = "'); DROP TABLE messages; --"
        add_message(session_id, "user", malicious, db_path=db_path)

        history = get_recent_history(session_id, db_path=db_path)
        check(len(history) == 1 and history[0]["content"] == malicious, "SQL-injection-style text is stored as plain data (parameterized queries), not executed")

        # Prove the messages table still exists and works normally afterward.
        add_message(session_id, "assistant", "still working fine", db_path=db_path)
        check(count_messages(session_id, db_path=db_path) == 2, "messages table still intact after injection-style content — no injection occurred")

    with_temp_db(_test)


def test_configurable_history_limit():
    def _test(db_path):
        session_id = create_session(db_path=db_path)
        for i in range(5):
            add_message(session_id, "user", f"m{i}", db_path=db_path)

        limited = get_recent_history(session_id, limit=2, db_path=db_path)
        check(len(limited) == 2, "custom limit=2 is respected")

        default = get_recent_history(session_id, db_path=db_path)
        check(len(default) == 5, "default limit (CONVERSATION_HISTORY_LIMIT=10) returns all 5 when fewer than 10 exist")

    with_temp_db(_test)


def test_build_conversation_context_format():
    def _test(db_path):
        session_id = create_session(db_path=db_path)
        add_message(session_id, "user", "hi", db_path=db_path)
        add_message(session_id, "assistant", "hello!", db_path=db_path)

        context = build_conversation_context(session_id, db_path=db_path)
        check(context == [{"role": "user", "content": "hi"}, {"role": "assistant", "content": "hello!"}], "build_conversation_context() returns plain role/content dicts, no timestamps, in order")

    with_temp_db(_test)


def run():
    tests = [
        test_database_initialization,
        test_session_creation_and_uniqueness,
        test_unknown_session_handling,
        test_storing_and_retrieving_messages,
        test_chronological_ordering,
        test_sliding_window_limit,
        test_session_isolation,
        test_invalid_role_rejected,
        test_empty_and_whitespace_message_rejected,
        test_extremely_long_message_handled,
        test_sql_injection_style_content_is_stored_safely,
        test_configurable_history_limit,
        test_build_conversation_context_format,
    ]

    for test in tests:
        print(f"\n--- {test.__name__} ---")
        test()

    print("\n" + "-" * 60)
    if _failures == 0:
        print("ALL CONVERSATION MEMORY TESTS PASSED (real SQLite, temporary database files)")
    else:
        print(f"{_failures} TEST(S) FAILED")


if __name__ == "__main__":
    run()
