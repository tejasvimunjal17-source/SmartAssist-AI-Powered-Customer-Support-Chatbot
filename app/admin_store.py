"""
Day 11: admin session storage + audit log, in their own SQLite database
(separate from conversations.db — different concern, different table
lifecycle).

Same patterns as Day 8's conversation_memory.py:
- every function takes an optional db_path for test isolation
- parameterized SQL only, never string-built queries
- sqlite3 is Python's standard library, so all of this is genuinely
  testable in this sandbox, no mocking required
"""

import secrets
import sqlite3
from datetime import datetime, timedelta, timezone
from typing import List, Optional

from app.config import ADMIN_DB_PATH, ADMIN_SESSION_TTL_MINUTES


def _get_connection(db_path: Optional[str] = None) -> sqlite3.Connection:
    conn = sqlite3.connect(db_path or ADMIN_DB_PATH)
    conn.row_factory = sqlite3.Row
    return conn


def init_admin_db(db_path: Optional[str] = None) -> None:
    conn = _get_connection(db_path)
    try:
        conn.execute(
            """
            CREATE TABLE IF NOT EXISTS admin_sessions (
                token TEXT PRIMARY KEY,
                created_at TEXT NOT NULL,
                expires_at TEXT NOT NULL
            )
            """
        )
        conn.execute(
            """
            CREATE TABLE IF NOT EXISTS audit_log (
                id INTEGER PRIMARY KEY AUTOINCREMENT,
                action TEXT NOT NULL,
                detail TEXT NOT NULL,
                status TEXT NOT NULL,
                created_at TEXT NOT NULL
            )
            """
        )
        conn.commit()
    finally:
        conn.close()


def create_admin_session(db_path: Optional[str] = None) -> str:
    """Creates a new random admin session token and stores its expiry."""
    token = secrets.token_urlsafe(32)
    now = datetime.now(timezone.utc)
    expires_at = now + timedelta(minutes=ADMIN_SESSION_TTL_MINUTES)

    conn = _get_connection(db_path)
    try:
        conn.execute(
            "INSERT INTO admin_sessions (token, created_at, expires_at) VALUES (?, ?, ?)",
            (token, now.isoformat(), expires_at.isoformat()),
        )
        conn.commit()
    finally:
        conn.close()

    return token


def validate_admin_session(token: Optional[str], db_path: Optional[str] = None) -> bool:
    """
    Returns True only if `token` exists AND has not expired. Missing,
    unknown, or expired tokens all safely return False (never raise).
    """
    if not token:
        return False

    conn = _get_connection(db_path)
    try:
        row = conn.execute(
            "SELECT expires_at FROM admin_sessions WHERE token = ?", (token,)
        ).fetchone()
    finally:
        conn.close()

    if row is None:
        return False

    expires_at = datetime.fromisoformat(row["expires_at"])
    return datetime.now(timezone.utc) < expires_at


def log_admin_action(action: str, detail: str = "", status: str = "success", db_path: Optional[str] = None) -> None:
    """
    Records one audit log entry. Never store secrets/passwords in
    `detail` — callers are responsible for only passing safe, non-secret
    descriptions (e.g. an article id, not a password).
    """
    created_at = datetime.now(timezone.utc).isoformat()
    conn = _get_connection(db_path)
    try:
        conn.execute(
            "INSERT INTO audit_log (action, detail, status, created_at) VALUES (?, ?, ?, ?)",
            (action, detail, status, created_at),
        )
        conn.commit()
    finally:
        conn.close()


def get_recent_logs(limit: int = 100, db_path: Optional[str] = None) -> List[dict]:
    """Returns the most recent audit log entries, newest first."""
    conn = _get_connection(db_path)
    try:
        rows = conn.execute(
            "SELECT action, detail, status, created_at FROM audit_log "
            "ORDER BY id DESC LIMIT ?",
            (limit,),
        ).fetchall()
    finally:
        conn.close()

    return [dict(row) for row in rows]
