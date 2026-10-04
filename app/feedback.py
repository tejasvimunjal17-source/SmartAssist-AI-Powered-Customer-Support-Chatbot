"""
Day 12: feedback (helpful / not helpful) on a specific assistant
message.

Lives in the SAME SQLite database as Day 8's sessions/messages (see
conversation_memory.init_db(), which also creates the `feedback` table)
— feedback is tightly coupled to a specific message within a specific
session, so splitting it into a separate database file would only make
the relationship harder to reason about and query.

SECURITY: a feedback submission is never trusted at face value. Before
storing anything, submit_feedback() verifies (via
conversation_memory.get_message) that message_id actually exists,
belongs to the claimed session_id, and is an "assistant" message — a
customer can't submit feedback against another session's message, a
nonexistent message, or their own message, just by guessing an id.

Re-rating: (session_id, message_id) has a UNIQUE constraint, so
submitting feedback twice for the same response UPDATES the existing
row (an intentional "change your rating" behavior) instead of creating
duplicate rows.
"""

import sqlite3
from datetime import datetime, timezone
from typing import List, Optional

from app.config import CONVERSATION_DB_PATH, MAX_FEEDBACK_COMMENT_CHARS, VALID_FEEDBACK_RATINGS
from app.conversation_memory import get_message


class FeedbackValidationError(ValueError):
    """Raised when a feedback submission fails validation."""


def _get_connection(db_path: Optional[str] = None) -> sqlite3.Connection:
    conn = sqlite3.connect(db_path or CONVERSATION_DB_PATH)
    conn.row_factory = sqlite3.Row
    return conn


def submit_feedback(
    session_id: str,
    message_id: int,
    rating: str,
    comment: Optional[str] = None,
    db_path: Optional[str] = None,
) -> dict:
    """
    Validates and stores one feedback submission. Raises
    FeedbackValidationError (never lets bad data reach the database) for:
    - an unknown rating value
    - a message_id that doesn't exist, doesn't belong to session_id, or
      isn't an assistant message
    - a comment longer than MAX_FEEDBACK_COMMENT_CHARS

    Returns a dict describing the stored row, including whether this was
    a new submission or an update to a previous rating for the same
    message.
    """
    if rating not in VALID_FEEDBACK_RATINGS:
        raise FeedbackValidationError(
            f"Invalid rating {rating!r}; must be one of {sorted(VALID_FEEDBACK_RATINGS)}"
        )

    comment = (comment or "").strip() or None
    if comment and len(comment) > MAX_FEEDBACK_COMMENT_CHARS:
        raise FeedbackValidationError(f"Comment must be {MAX_FEEDBACK_COMMENT_CHARS} characters or fewer")

    message = get_message(session_id, message_id, db_path=db_path)
    if message is None:
        raise FeedbackValidationError("The referenced message does not exist for this session")
    if message["role"] != "assistant":
        raise FeedbackValidationError("Feedback can only be submitted for assistant messages")

    created_at = datetime.now(timezone.utc).isoformat()

    conn = _get_connection(db_path)
    try:
        existing = conn.execute(
            "SELECT id FROM feedback WHERE session_id = ? AND message_id = ?",
            (session_id, message_id),
        ).fetchone()

        if existing:
            conn.execute(
                "UPDATE feedback SET rating = ?, comment = ?, created_at = ? WHERE id = ?",
                (rating, comment, created_at, existing["id"]),
            )
            feedback_id = existing["id"]
            updated = True
        else:
            cursor = conn.execute(
                "INSERT INTO feedback (session_id, message_id, rating, comment, created_at) "
                "VALUES (?, ?, ?, ?, ?)",
                (session_id, message_id, rating, comment, created_at),
            )
            feedback_id = cursor.lastrowid
            updated = False

        conn.commit()
    finally:
        conn.close()

    return {
        "id": feedback_id,
        "session_id": session_id,
        "message_id": message_id,
        "rating": rating,
        "comment": comment,
        "created_at": created_at,
        "updated": updated,
    }


def get_recent_feedback(limit: int = 100, db_path: Optional[str] = None) -> List[dict]:
    """Returns the most recent feedback entries across all sessions, newest first (for the admin view)."""
    conn = _get_connection(db_path)
    try:
        rows = conn.execute(
            "SELECT id, session_id, message_id, rating, comment, created_at FROM feedback "
            "ORDER BY id DESC LIMIT ?",
            (limit,),
        ).fetchall()
    finally:
        conn.close()

    return [dict(row) for row in rows]
