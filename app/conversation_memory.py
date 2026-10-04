"""
Day 8: SQLite conversation memory — the persistence layer for sessions
and messages. SQLite is the SOURCE OF TRUTH here; there is no in-memory
dictionary standing in for it.

Two tables:
    sessions(session_id TEXT PRIMARY KEY, created_at TEXT)
    messages(id INTEGER PRIMARY KEY AUTOINCREMENT, session_id TEXT,
             role TEXT, content TEXT, created_at TEXT)

Design notes:
- Every function accepts an optional `db_path` argument (defaulting to
  app.config.CONVERSATION_DB_PATH). This is dependency injection again,
  same pattern as Day 4/5/6/7 — it's what lets tests use a temporary
  database file instead of touching the real conversations.db.
- All SQL uses parameterized queries ("?" placeholders) — user message
  content is NEVER concatenated into a SQL string. This is the standard
  defense against SQL injection.
- Older messages are NEVER deleted by this module. The sliding window
  (see get_recent_history) only limits what's *retrieved*, not what's
  stored.

ENVIRONMENT NOTE: sqlite3 is part of Python's standard library, so
(unlike sentence-transformers/chromadb/Gemini in earlier days) this
module could be, and was, fully executed and tested for real in this
sandbox — see the Day 8 test results in the final summary.
"""

import os
import sqlite3
import uuid
from datetime import datetime, timezone
from typing import List, Optional

from app.config import CONVERSATION_DB_PATH, CONVERSATION_HISTORY_LIMIT, VALID_MESSAGE_ROLES


def _get_connection(db_path: Optional[str] = None) -> sqlite3.Connection:
    path = db_path or CONVERSATION_DB_PATH
    conn = sqlite3.connect(path)
    conn.row_factory = sqlite3.Row
    conn.execute("PRAGMA foreign_keys = ON")
    return conn


def init_db(db_path: Optional[str] = None) -> None:
    """
    Creates the sessions/messages tables (and a helpful index) if they
    don't already exist. Safe to call every time the app starts.
    """
    conn = _get_connection(db_path)
    try:
        conn.execute(
            """
            CREATE TABLE IF NOT EXISTS sessions (
                session_id TEXT PRIMARY KEY,
                created_at TEXT NOT NULL
            )
            """
        )
        conn.execute(
            """
            CREATE TABLE IF NOT EXISTS messages (
                id INTEGER PRIMARY KEY AUTOINCREMENT,
                session_id TEXT NOT NULL,
                role TEXT NOT NULL,
                content TEXT NOT NULL,
                created_at TEXT NOT NULL,
                FOREIGN KEY (session_id) REFERENCES sessions (session_id)
            )
            """
        )
        # Speeds up "recent messages for this session" queries, which is
        # by far the most common access pattern.
        conn.execute(
            "CREATE INDEX IF NOT EXISTS idx_messages_session_created "
            "ON messages (session_id, created_at)"
        )
        # Day 12: feedback on a specific assistant message. Lives in the
        # same database file as sessions/messages (not a separate db)
        # because it's tightly coupled to them — feedback always refers
        # to one specific message within one specific session.
        # UNIQUE(session_id, message_id) is what makes re-rating the same
        # response an UPDATE instead of a second row (see app/feedback.py).
        conn.execute(
            """
            CREATE TABLE IF NOT EXISTS feedback (
                id INTEGER PRIMARY KEY AUTOINCREMENT,
                session_id TEXT NOT NULL,
                message_id INTEGER NOT NULL,
                rating TEXT NOT NULL,
                comment TEXT,
                created_at TEXT NOT NULL,
                UNIQUE (session_id, message_id),
                FOREIGN KEY (session_id) REFERENCES sessions (session_id),
                FOREIGN KEY (message_id) REFERENCES messages (id)
            )
            """
        )
        conn.commit()
    finally:
        conn.close()


def create_session(db_path: Optional[str] = None) -> str:
    """Creates a new session with a unique id and returns that id."""
    session_id = str(uuid.uuid4())
    created_at = datetime.now(timezone.utc).isoformat()

    conn = _get_connection(db_path)
    try:
        conn.execute(
            "INSERT INTO sessions (session_id, created_at) VALUES (?, ?)",
            (session_id, created_at),
        )
        conn.commit()
    finally:
        conn.close()

    return session_id


def session_exists(session_id: str, db_path: Optional[str] = None) -> bool:
    if not session_id:
        return False

    conn = _get_connection(db_path)
    try:
        row = conn.execute(
            "SELECT 1 FROM sessions WHERE session_id = ?", (session_id,)
        ).fetchone()
        return row is not None
    finally:
        conn.close()


def add_message(session_id: str, role: str, content: str, db_path: Optional[str] = None) -> int:
    """
    Stores one message and returns its new row id (used by Day 12's
    feedback system to associate feedback with a specific assistant
    message). Validates before touching the database:
    - session must already exist (call create_session() first)
    - role must be "user" or "assistant"
    - content must not be empty/whitespace-only

    Raises ValueError on any of these instead of silently storing bad
    data (per the brief: "invalid roles or malformed messages [must]
    not silently enter the database").
    """
    if not session_exists(session_id, db_path):
        raise ValueError(f"Cannot add message: session {session_id!r} does not exist")

    if role not in VALID_MESSAGE_ROLES:
        raise ValueError(f"Invalid role {role!r}; must be one of {sorted(VALID_MESSAGE_ROLES)}")

    if not content or not content.strip():
        raise ValueError("Message content cannot be empty")

    created_at = datetime.now(timezone.utc).isoformat()

    conn = _get_connection(db_path)
    try:
        cursor = conn.execute(
            "INSERT INTO messages (session_id, role, content, created_at) VALUES (?, ?, ?, ?)",
            (session_id, role, content, created_at),
        )
        conn.commit()
        return cursor.lastrowid
    finally:
        conn.close()


def get_recent_history(
    session_id: str,
    limit: int = CONVERSATION_HISTORY_LIMIT,
    db_path: Optional[str] = None,
) -> List[dict]:
    """
    Returns the most recent `limit` messages for this session, in
    CHRONOLOGICAL order (oldest of the recent batch first) — the order
    an LLM should read them in. Unknown session -> [] (not an error).

    Day 12 addition: each item now also includes "id" (the message's row
    id), so the frontend can attach feedback controls to a specific
    assistant message even after a page reload / history reload. This is
    an additive field — existing code reading role/content/timestamp is
    unaffected.
    """
    if not session_exists(session_id, db_path):
        return []

    conn = _get_connection(db_path)
    try:
        rows = conn.execute(
            """
            SELECT id, role, content, created_at FROM messages
            WHERE session_id = ?
            ORDER BY id DESC
            LIMIT ?
            """,
            (session_id, limit),
        ).fetchall()
    finally:
        conn.close()

    # We fetched newest-first (for an efficient LIMIT), then reverse to
    # chronological order before returning.
    return [
        {"id": row["id"], "role": row["role"], "content": row["content"], "timestamp": row["created_at"]}
        for row in reversed(rows)
    ]


def get_message(session_id: str, message_id: int, db_path: Optional[str] = None) -> Optional[dict]:
    """
    Looks up one specific message, scoped to `session_id` — used by Day
    12's feedback system to verify a feedback submission actually refers
    to a real message that belongs to the claimed session, instead of
    trusting message_id/session_id from the request at face value.
    Returns None if no such message exists in that session.
    """
    conn = _get_connection(db_path)
    try:
        row = conn.execute(
            "SELECT id, role, content, created_at FROM messages WHERE session_id = ? AND id = ?",
            (session_id, message_id),
        ).fetchone()
    finally:
        conn.close()

    if row is None:
        return None
    return {"id": row["id"], "role": row["role"], "content": row["content"], "timestamp": row["created_at"]}


def count_messages(session_id: str, db_path: Optional[str] = None) -> int:
    """Total stored message count for a session (ignores the sliding window) — used to confirm older messages are never deleted."""
    conn = _get_connection(db_path)
    try:
        row = conn.execute(
            "SELECT COUNT(*) AS c FROM messages WHERE session_id = ?", (session_id,)
        ).fetchone()
        return row["c"] if row else 0
    finally:
        conn.close()
