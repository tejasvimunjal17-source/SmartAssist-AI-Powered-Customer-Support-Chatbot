"""
Day 13: runs every evaluation and assembles one results dictionary.

The most important job of this module is HONESTY about what could and
couldn't be measured in the environment it runs in:

- Intent: always runs (the rule layer needs no dependencies), but records
  whether the embedding-based fallback was actually available. Without
  it, any message no keyword rule matches simply becomes "unknown" — the
  results are then labeled as rule-layer-only, not as full-system results.
- Retrieval: needs sentence-transformers + ChromaDB + a built index. If
  any is missing, the section is marked UNAVAILABLE and NO retrieval
  numbers are produced. (Calling app.evaluation.evaluate() without those
  would silently return empty results for every query and yield a
  misleading 0% hit rate, so it is never called in that case.)
- Escalation: runs on synthetic scenarios with fixed intent confidence,
  so it is deterministic in every environment.
- Feedback: reads only REAL stored feedback. If there is none (or no
  database file), it says so and does NOT create a database or invent data.

All external dependencies are injectable so tests can exercise every
branch with deterministic fakes.
"""

import os
import platform
import sqlite3
from datetime import datetime, timezone
from typing import Callable, List, Optional, Tuple

from app.config import CONVERSATION_DB_PATH, DEFAULT_TOP_K, RELEVANCE_DISTANCE_THRESHOLD
from app.evaluation import SAMPLE_QUERIES
from app.evaluation import evaluate as run_retrieval_evaluation
from app.evaluation_data import ESCALATION_EVALUATION_SCENARIOS, INTENT_EVALUATION_DATASET
from app.evaluation_metrics import (
    evaluate_escalation_scenarios,
    evaluate_intents,
    evaluate_retrieval_results,
    summarize_feedback,
)
from app.feedback import get_recent_feedback


def check_semantic_available() -> bool:
    """True only if the real embedding model can actually encode text right now."""
    try:
        from app.embeddings import embed_texts

        embed_texts(["availability check"])
        return True
    except Exception:
        return False


def check_retrieval_available() -> Tuple[bool, str]:
    """
    Returns (available, reason_if_not). Retrieval evaluation needs the
    embedding model, ChromaDB, AND a non-empty built index (Day 4).
    """
    try:
        from app.vector_store import get_collection

        collection = get_collection()
        indexed = collection.count()
    except Exception as exc:
        return False, f"vector store unavailable ({type(exc).__name__}: {exc})"

    if indexed == 0:
        return False, "the vector index is empty — run `python -m scripts.build_index` first"

    if not check_semantic_available():
        return False, "the embedding model (sentence-transformers) is unavailable"

    return True, ""


def load_feedback_entries(db_path: Optional[str] = None) -> Optional[List[dict]]:
    """
    Reads stored feedback WITHOUT creating anything: returns None if the
    database file doesn't exist or has no feedback table (sqlite3.connect
    would otherwise silently create an empty database file).
    """
    path = db_path or CONVERSATION_DB_PATH
    if not os.path.exists(path):
        return None
    try:
        return get_recent_feedback(limit=100000, db_path=path)
    except sqlite3.Error:
        return None


def run_all_evaluations(
    generated_at: Optional[str] = None,
    classify_fn: Optional[Callable] = None,
    escalate_fn: Optional[Callable] = None,
    retrieve_fn: Optional[Callable] = None,
    semantic_check_fn: Optional[Callable[[], bool]] = None,
    retrieval_check_fn: Optional[Callable[[], Tuple[bool, str]]] = None,
    feedback_db_path: Optional[str] = None,
    intent_dataset: Optional[List[dict]] = None,
    escalation_scenarios: Optional[List[dict]] = None,
) -> dict:
    semantic_available = (semantic_check_fn or check_semantic_available)()
    retrieval_available, retrieval_reason = (retrieval_check_fn or check_retrieval_available)()

    # --- Intent ---
    intent_metrics = evaluate_intents(
        INTENT_EVALUATION_DATASET if intent_dataset is None else intent_dataset,
        classify_fn=classify_fn,
    )
    if semantic_available:
        intent_label = "MEASURED on the hand-written evaluation dataset (full system: rules + embedding fallback)"
    else:
        intent_label = (
            "PARTIAL — measured on the hand-written evaluation dataset with the RULE LAYER ONLY. "
            "The embedding fallback was unavailable in this environment, so messages no rule matched "
            "became 'unknown'. Re-run in an environment with sentence-transformers for full-system numbers."
        )

    # --- Retrieval ---
    if retrieval_available:
        eval_results = run_retrieval_evaluation(top_k=DEFAULT_TOP_K, retrieve_fn=retrieve_fn)
        retrieval_section = {
            "status": "measured",
            "label": "MEASURED on the 12 Day 5 sample queries (category-level) against the real index",
            "reason": None,
            "metrics": evaluate_retrieval_results(eval_results),
        }
    else:
        retrieval_section = {
            "status": "unavailable",
            "label": "UNAVAILABLE in this environment — no retrieval metrics were produced",
            "reason": retrieval_reason,
            "metrics": None,
        }

    # --- Escalation ---
    escalation_metrics = evaluate_escalation_scenarios(
        ESCALATION_EVALUATION_SCENARIOS if escalation_scenarios is None else escalation_scenarios,
        escalate_fn=escalate_fn,
    )

    # --- Feedback ---
    entries = load_feedback_entries(feedback_db_path)
    feedback_metrics = summarize_feedback(entries)
    if entries is None:
        feedback_label = "NO DATA — no feedback database or feedback table found"
    elif feedback_metrics["available"]:
        feedback_label = "MEASURED from real stored feedback"
    else:
        feedback_label = "NO DATA — the feedback table is empty"

    return {
        "meta": {
            "generated_at": generated_at or datetime.now(timezone.utc).isoformat(timespec="seconds"),
            "python_version": platform.python_version(),
            "semantic_fallback_available": semantic_available,
            "retrieval_available": retrieval_available,
            "retrieval_top_k": DEFAULT_TOP_K,
            "relevance_distance_threshold": RELEVANCE_DISTANCE_THRESHOLD,
        },
        "intent": {
            "label": intent_label,
            "metrics": intent_metrics,
        },
        "retrieval": retrieval_section,
        "escalation": {
            "label": "MEASURED on synthetic scenarios with FIXED intent confidences (tests decision logic, not real traffic)",
            "metrics": escalation_metrics,
        },
        "feedback": {
            "label": feedback_label,
            "metrics": feedback_metrics,
        },
        "retrieval_sample_query_count": len(SAMPLE_QUERIES),
    }
