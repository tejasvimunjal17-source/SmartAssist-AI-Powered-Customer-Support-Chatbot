"""
Day 8: turns raw stored messages (from app.conversation_memory) into the
plain format a future response-generation step will consume — just a
chronological list of {"role": ..., "content": ...} dicts.

Kept as its OWN module (not inside conversation_memory.py, and not
inside llm_provider.py/response_generator.py) so:
- the database layer doesn't need to know anything about prompts, and
- the LLM/provider layer doesn't need to know anything about SQLite.
Whoever wires the full chat pipeline together later imports this
function, not the database module directly.
"""

from typing import List

from app.conversation_memory import get_recent_history


def build_conversation_context(session_id: str, limit: int = None, db_path: str = None) -> List[dict]:
    """
    Returns the recent conversation history for `session_id` as a plain
    list of {"role": "user"|"assistant", "content": "..."} dicts, oldest
    first. Returns [] for an unknown/empty session — never raises.
    """
    kwargs = {}
    if limit is not None:
        kwargs["limit"] = limit

    history = get_recent_history(session_id, db_path=db_path, **kwargs)
    return [{"role": item["role"], "content": item["content"]} for item in history]
