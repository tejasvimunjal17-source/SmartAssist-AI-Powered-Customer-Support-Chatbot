"""
Day 13: evaluation metric calculations.

This module only COMPUTES metrics from data handed to it. It never
invents numbers:
- undefined values (e.g. accuracy over zero samples) are returned as
  None, not 0.0 — "0% accuracy" and "no data" mean very different things
- malformed evaluation records are skipped and COUNTED
  (`skipped_malformed`) instead of crashing or silently vanishing
- all classifier / escalation / retrieval functions are injected (with
  the real ones as defaults), so the calculations can be verified with
  deterministic fakes without sentence-transformers, ChromaDB, or an API

Results describe performance on a specific evaluation dataset. They are
NOT estimates of real-world production performance.
"""

from typing import Callable, Dict, List, Optional

from app.escalation import evaluate_escalation
from app.intent_router import INTENT_UNKNOWN, IntentResult, classify_intent

INTENT_LABELS = ["greeting", "faq", "complaint", "technical_issue", "escalation"]

MAX_FEEDBACK_COMMENTS_IN_SUMMARY = 20
MAX_COMMENT_DISPLAY_CHARS = 200


def safe_div(numerator: float, denominator: float) -> Optional[float]:
    """Division that returns None (undefined) instead of raising or faking 0.0 when the denominator is 0."""
    if not denominator:
        return None
    return round(numerator / denominator, 4)


def _mean(values: List[float]) -> Optional[float]:
    if not values:
        return None
    return round(sum(values) / len(values), 4)


# --- Intent classification -------------------------------------------------

def _valid_intent_record(item) -> bool:
    return (
        isinstance(item, dict)
        and isinstance(item.get("text"), str)
        and bool(item["text"].strip())
        and item.get("expected_intent") in INTENT_LABELS
    )


def evaluate_intents(dataset: List[dict], classify_fn: Optional[Callable[[str], IntentResult]] = None) -> dict:
    """
    Runs every valid record through the intent classifier and computes
    accuracy, per-intent precision/recall, a confusion matrix, and
    confidence/method breakdowns.
    """
    classify_fn = classify_fn or classify_intent

    records = []
    skipped = 0
    for item in dataset or []:
        if not _valid_intent_record(item):
            skipped += 1
            continue
        result = classify_fn(item["text"])
        records.append(
            {
                "text": item["text"],
                "expected": item["expected_intent"],
                "predicted": result.intent,
                "confidence": result.confidence,
                "method": result.method,
                "correct": result.intent == item["expected_intent"],
            }
        )

    total = len(records)
    correct = sum(1 for r in records if r["correct"])

    predicted_labels = INTENT_LABELS + [INTENT_UNKNOWN]
    confusion: Dict[str, Dict[str, int]] = {
        expected: {predicted: 0 for predicted in predicted_labels} for expected in INTENT_LABELS
    }
    for r in records:
        column = r["predicted"] if r["predicted"] in predicted_labels else INTENT_UNKNOWN
        confusion[r["expected"]][column] += 1

    per_intent = {}
    for label in INTENT_LABELS:
        support = sum(1 for r in records if r["expected"] == label)
        true_positives = sum(1 for r in records if r["expected"] == label and r["predicted"] == label)
        predicted_count = sum(1 for r in records if r["predicted"] == label)
        per_intent[label] = {
            "support": support,
            "correct": true_positives,
            "predicted_count": predicted_count,
            "precision": safe_div(true_positives, predicted_count),
            "recall": safe_div(true_positives, support),
        }

    method_breakdown = {}
    for method in sorted({r["method"] for r in records}):
        of_method = [r for r in records if r["method"] == method]
        method_breakdown[method] = {
            "count": len(of_method),
            "correct": sum(1 for r in of_method if r["correct"]),
            "accuracy": safe_div(sum(1 for r in of_method if r["correct"]), len(of_method)),
        }

    return {
        "total_samples": total,
        "skipped_malformed": skipped,
        "correct": correct,
        "incorrect": total - correct,
        "accuracy": safe_div(correct, total),
        "unknown_predictions": sum(1 for r in records if r["predicted"] == INTENT_UNKNOWN),
        "per_intent": per_intent,
        "confusion_matrix": confusion,
        "method_breakdown": method_breakdown,
        "mean_confidence_correct": _mean([r["confidence"] for r in records if r["correct"]]),
        "mean_confidence_incorrect": _mean([r["confidence"] for r in records if not r["correct"]]),
        "misclassified": [
            {k: r[k] for k in ("text", "expected", "predicted", "confidence", "method")}
            for r in records
            if not r["correct"]
        ],
    }


# --- Retrieval / relevance -------------------------------------------------

def _valid_retrieval_result(item) -> bool:
    return (
        hasattr(item, "query")
        and hasattr(item, "expected_category")
        and isinstance(getattr(item, "retrieved", None), list)
    )


def evaluate_retrieval_results(eval_results: list) -> dict:
    """
    Computes retrieval metrics from Day 5 EvalResult objects (as produced
    by app.evaluation.evaluate). Evaluation is at CATEGORY level, because
    the sample queries are labeled with an expected category, not an
    expected individual article.

    - topk_hit_rate:       expected category appears anywhere in the K results
    - top1_hit_rate:       the single best result has the expected category
    - relevant_hit_rate:   expected category appears among results that
                           PASSED the relevance threshold (is_relevant=True)
    - filtered_out_results: results returned but below the threshold
    """
    valid = [r for r in (eval_results or []) if _valid_retrieval_result(r)]
    skipped = len(eval_results or []) - len(valid)
    total = len(valid)

    topk_hits = top1_hits = relevant_hits = 0
    returned_counts, relevant_counts = [], []
    no_result_queries = 0
    filtered_out = 0
    top1_distance_hits, top1_distance_misses = [], []
    per_category: Dict[str, Dict[str, int]] = {}

    for r in valid:
        retrieved = r.retrieved
        returned_counts.append(len(retrieved))
        relevant_in_result = [a for a in retrieved if a.is_relevant]
        relevant_counts.append(len(relevant_in_result))
        filtered_out += len(retrieved) - len(relevant_in_result)

        if not retrieved:
            no_result_queries += 1

        topk_hit = any(a.category == r.expected_category for a in retrieved)
        top1_hit = bool(retrieved) and retrieved[0].category == r.expected_category
        relevant_hit = any(a.category == r.expected_category for a in relevant_in_result)

        topk_hits += topk_hit
        top1_hits += top1_hit
        relevant_hits += relevant_hit

        if retrieved:
            (top1_distance_hits if top1_hit else top1_distance_misses).append(retrieved[0].distance)

        bucket = per_category.setdefault(r.expected_category, {"queries": 0, "topk_hits": 0, "top1_hits": 0})
        bucket["queries"] += 1
        bucket["topk_hits"] += int(topk_hit)
        bucket["top1_hits"] += int(top1_hit)

    return {
        "total_queries": total,
        "skipped_malformed": skipped,
        "topk_hit_rate": safe_div(topk_hits, total),
        "top1_hit_rate": safe_div(top1_hits, total),
        "relevant_hit_rate": safe_div(relevant_hits, total),
        "topk_hits": topk_hits,
        "top1_hits": top1_hits,
        "relevant_hits": relevant_hits,
        "avg_results_returned": _mean(returned_counts),
        "avg_relevant_results": _mean(relevant_counts),
        "filtered_out_results": filtered_out,
        "queries_with_no_results": no_result_queries,
        "mean_top1_distance_when_correct": _mean(top1_distance_hits),
        "mean_top1_distance_when_wrong": _mean(top1_distance_misses),
        "per_category": per_category,
    }


# --- Escalation ---------------------------------------------------------------

def _valid_escalation_scenario(item) -> bool:
    return (
        isinstance(item, dict)
        and isinstance(item.get("message"), str)
        and isinstance(item.get("expected_escalate"), bool)
        and isinstance(item.get("intent"), str)
        and isinstance(item.get("confidence"), (int, float))
        and not isinstance(item.get("confidence"), bool)
    )


def evaluate_escalation_scenarios(scenarios: List[dict], escalate_fn: Optional[Callable] = None) -> dict:
    """
    Runs each scenario through the Day 9 escalation decision logic using
    the scenario's FIXED (synthetic) intent confidence, and compares the
    decision with the expected one.

    Terminology: "positive" = the decision to escalate.
    - false escalation  = escalated, but expected not to
    - missed escalation = did not escalate, but expected to
    """
    escalate_fn = escalate_fn or evaluate_escalation

    outcomes = []
    skipped = 0
    for scenario in scenarios or []:
        if not _valid_escalation_scenario(scenario):
            skipped += 1
            continue
        intent_result = IntentResult(
            intent=scenario["intent"],
            confidence=float(scenario["confidence"]),
            method="synthetic",
            evidence="fixed evaluation input",
        )
        decision = escalate_fn(
            scenario["message"],
            intent_result=intent_result,
            recent_user_messages=scenario.get("recent_user_messages"),
        )
        outcomes.append(
            {
                "name": scenario.get("name", scenario["message"][:40]),
                "category": scenario.get("category", "uncategorized"),
                "intent": scenario["intent"],
                "confidence": float(scenario["confidence"]),
                "expected_escalate": scenario["expected_escalate"],
                "actual_escalate": decision.should_escalate,
                "expected_reason": scenario.get("expected_reason"),
                "actual_reason": decision.reason,
                "correct": decision.should_escalate == scenario["expected_escalate"],
            }
        )

    total = len(outcomes)
    tp = sum(1 for o in outcomes if o["expected_escalate"] and o["actual_escalate"])
    fp = sum(1 for o in outcomes if not o["expected_escalate"] and o["actual_escalate"])
    fn = sum(1 for o in outcomes if o["expected_escalate"] and not o["actual_escalate"])
    tn = sum(1 for o in outcomes if not o["expected_escalate"] and not o["actual_escalate"])

    reason_checked = [o for o in outcomes if o["expected_escalate"] and o["actual_escalate"] and o["expected_reason"]]
    reason_correct = sum(1 for o in reason_checked if o["actual_reason"] == o["expected_reason"])

    per_category: Dict[str, Dict[str, int]] = {}
    for o in outcomes:
        bucket = per_category.setdefault(o["category"], {"scenarios": 0, "correct": 0})
        bucket["scenarios"] += 1
        bucket["correct"] += int(o["correct"])

    return {
        "total_scenarios": total,
        "skipped_malformed": skipped,
        "correct_decisions": sum(1 for o in outcomes if o["correct"]),
        "decision_accuracy": safe_div(sum(1 for o in outcomes if o["correct"]), total),
        "escalations_made": tp + fp,
        "true_escalations": tp,
        "false_escalations": fp,
        "missed_escalations": fn,
        "correct_non_escalations": tn,
        "escalation_precision": safe_div(tp, tp + fp),
        "escalation_recall": safe_div(tp, tp + fn),
        "reason_accuracy_when_correctly_escalated": safe_div(reason_correct, len(reason_checked)),
        "reasons_checked": len(reason_checked),
        "per_category": per_category,
        "false_escalation_cases": [o["name"] for o in outcomes if not o["expected_escalate"] and o["actual_escalate"]],
        "missed_escalation_cases": [o["name"] for o in outcomes if o["expected_escalate"] and not o["actual_escalate"]],
        "missed_escalation_details": [
            {"name": o["name"], "intent": o["intent"], "confidence": o["confidence"]}
            for o in outcomes
            if o["expected_escalate"] and not o["actual_escalate"]
        ],
        "wrong_reason_cases": [
            {"name": o["name"], "expected_reason": o["expected_reason"], "actual_reason": o["actual_reason"]}
            for o in reason_checked
            if o["actual_reason"] != o["expected_reason"]
        ],
    }


# --- Feedback ------------------------------------------------------------------

NO_FEEDBACK_MESSAGE = "No production/user feedback data available."


def _clean_comment(comment: str) -> str:
    collapsed = " ".join(comment.split())
    if len(collapsed) > MAX_COMMENT_DISPLAY_CHARS:
        return collapsed[:MAX_COMMENT_DISPLAY_CHARS] + "…"
    return collapsed


def summarize_feedback(entries: Optional[List[dict]]) -> dict:
    """
    Summarizes stored feedback (Day 12). Reports counts and the helpful
    percentage only — comments are exposed as-is for HUMAN reading, and no
    sentiment conclusions are drawn from them. Entries whose rating is not
    exactly "helpful"/"not_helpful" are counted as invalid, not guessed at.
    """
    valid_entries = [e for e in (entries or []) if isinstance(e, dict)]
    if not valid_entries:
        return {
            "available": False,
            "message": NO_FEEDBACK_MESSAGE,
            "total_feedback": 0,
            "helpful": 0,
            "not_helpful": 0,
            "invalid_rating_entries": 0,
            "helpful_percentage": None,
            "comments": [],
        }

    helpful = sum(1 for e in valid_entries if e.get("rating") == "helpful")
    not_helpful = sum(1 for e in valid_entries if e.get("rating") == "not_helpful")
    invalid = len(valid_entries) - helpful - not_helpful
    rated = helpful + not_helpful

    percentage = safe_div(helpful * 100, rated)

    comments = [
        {"rating": e.get("rating"), "comment": _clean_comment(e["comment"])}
        for e in valid_entries
        if isinstance(e.get("comment"), str) and e["comment"].strip()
    ][:MAX_FEEDBACK_COMMENTS_IN_SUMMARY]

    return {
        "available": True,
        "message": None,
        "total_feedback": len(valid_entries),
        "helpful": helpful,
        "not_helpful": not_helpful,
        "invalid_rating_entries": invalid,
        "helpful_percentage": percentage,
        "comments": comments,
    }
