"""
Duplicate-request guard: only one /chat request may be in flight per
session at a time. A double-tap (or a retrying client) would otherwise
store the user's message twice and pay for two Gemini calls.

In-memory and per-process, which matches this app's single-process
deployment. Holds only opaque session ids, never message content.
"""

import threading
from typing import Set

_lock = threading.Lock()
_active: Set[str] = set()


def try_acquire(session_id: str) -> bool:
    with _lock:
        if session_id in _active:
            return False
        _active.add(session_id)
        return True


def release(session_id: str) -> None:
    with _lock:
        _active.discard(session_id)
