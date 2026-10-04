"""
Day 13 tests for the evaluation system.

Everything here is deterministic and needs no external service:
- metric functions are checked against values computed BY HAND (the
  expected numbers are written out in comments, so the tests verify the
  math rather than merely running the code)
- classifiers / retrieval / escalation are replaced by small fakes
- feedback tests use temporary SQLite databases containing clearly
  synthetic test rows (these rows exist only inside the tests and are
  never added to any real database or report)

Run with:
    python -m tests.test_evaluation_metrics
"""

import json
import os
import shutil
import sys
import tempfile

from app.conversation_memory import add_message, create_session, init_db
from app.evaluation import SAMPLE_QUERIES, EvalResult
from app.evaluation_data import ESCALATION_EVALUATION_SCENARIOS, INTENT_EVALUATION_DATASET
from app.evaluation_metrics import (
    INTENT_LABELS,
    NO_FEEDBACK_MESSAGE,
    evaluate_escalation_scenarios,
    evaluate_intents,
    evaluate_retrieval_results,
    safe_div,
    summarize_feedback,
)
from app.evaluation_report import _cell, build_markdown_report, derive_findings, write_reports
from app.evaluation_runner import load_feedback_entries, run_all_evaluations
from app.feedback import submit_feedback
from app.intent_router import IntentResult
from app.retrieval import RetrievedArticle

_failures = 0


def check(condition, description):
    global _failures
    status = "PASS" if condition else "FAIL"
    print(f"[{status}] {description}")
    if not condition:
        _failures += 1


def article(category, distance, relevant):
    return RetrievedArticle(
        id=f"{category}-x", title="t", category=category, content="c",
        source_filename="f.md", source_path="/kb/f.md", distance=distance, is_relevant=relevant,
    )


class Decision:
    def __init__(self, should_escalate, reason):
        self.should_escalate = should_escalate
        self.reason = reason


# --- safe_div / zero division -------------------------------------------------

def test_safe_div():
    check(safe_div(1, 0) is None, "division by zero returns None (undefined), not 0.0 or an exception")
    check(safe_div(0, 5) == 0.0, "0 / 5 is a real, defined 0.0")
    check(safe_div(1, 3) == 0.3333, "1 / 3 rounds to 0.3333")


# --- Intent metrics -----------------------------------------------------------

def test_intent_metrics_hand_computed():
    dataset = [
        {"text": "a", "expected_intent": "greeting"},
        {"text": "b", "expected_intent": "greeting"},
        {"text": "c", "expected_intent": "faq"},
        {"text": "d", "expected_intent": "complaint"},
        {"text": "e", "expected_intent": "escalation"},
    ]
    fake = {
        "a": IntentResult("greeting", 0.95, "rule", ""),
        "b": IntentResult("unknown", 0.0, "none", ""),
        "c": IntentResult("faq", 0.8, "semantic", ""),
        "d": IntentResult("faq", 0.7, "semantic", ""),   # wrong: complaint predicted as faq
        "e": IntentResult("escalation", 0.95, "rule", ""),
    }
    m = evaluate_intents(dataset, classify_fn=lambda text: fake[text])

    check(m["total_samples"] == 5 and m["correct"] == 3 and m["incorrect"] == 2, "totals: 5 samples, 3 correct, 2 incorrect")
    check(m["accuracy"] == 0.6, "accuracy = 3/5 = 0.6")
    check(m["unknown_predictions"] == 1, "one 'unknown' prediction counted")

    g, f, c, t, e = (m["per_intent"][k] for k in ["greeting", "faq", "complaint", "technical_issue", "escalation"])
    check(g["precision"] == 1.0 and g["recall"] == 0.5, "greeting: precision 1/1, recall 1/2")
    check(f["precision"] == 0.5 and f["recall"] == 1.0, "faq: precision 1/2 (complaint wrongly predicted faq), recall 1/1")
    check(c["precision"] is None and c["recall"] == 0.0, "complaint: precision undefined (never predicted), recall 0/1")
    check(t["support"] == 0 and t["precision"] is None and t["recall"] is None, "technical_issue: no samples -> precision and recall both undefined (None)")
    check(e["precision"] == 1.0 and e["recall"] == 1.0, "escalation: perfect")

    cm = m["confusion_matrix"]
    check(cm["greeting"]["greeting"] == 1 and cm["greeting"]["unknown"] == 1, "confusion: greeting row = 1 correct, 1 unknown")
    check(cm["complaint"]["faq"] == 1, "confusion: complaint mistaken for faq once")
    check(sum(sum(row.values()) for row in cm.values()) == 5, "confusion matrix cells sum to the total sample count")

    mb = m["method_breakdown"]
    check(mb["rule"] == {"count": 2, "correct": 2, "accuracy": 1.0}, "method breakdown: rule 2/2")
    check(mb["none"]["accuracy"] == 0.0 and mb["semantic"]["accuracy"] == 0.5, "method breakdown: none 0/1, semantic 1/2")

    check(m["mean_confidence_correct"] == 0.9, "mean confidence when correct = (0.95+0.8+0.95)/3 = 0.9")
    check(m["mean_confidence_incorrect"] == 0.35, "mean confidence when incorrect = (0.0+0.7)/2 = 0.35")
    check(len(m["misclassified"]) == 2 and {x["text"] for x in m["misclassified"]} == {"b", "d"}, "misclassified list contains exactly the 2 wrong samples")


def test_intent_empty_and_malformed():
    for empty in ([], None):
        m = evaluate_intents(empty, classify_fn=lambda t: IntentResult("faq", 1.0, "rule", ""))
        check(m["total_samples"] == 0 and m["accuracy"] is None, f"empty dataset ({empty!r}) -> total 0, accuracy None (no crash, no fake 0%)")
        check(m["misclassified"] == [] and m["mean_confidence_correct"] is None, f"empty dataset ({empty!r}) -> no misclassified, undefined mean confidence")

    malformed = [
        None, "just a string", {"text": "no label"}, {"expected_intent": "faq"},
        {"text": "   ", "expected_intent": "faq"}, {"text": "bad label", "expected_intent": "not-a-label"},
        {"text": 123, "expected_intent": "faq"}, {"text": "good", "expected_intent": "faq"},
    ]
    m = evaluate_intents(malformed, classify_fn=lambda t: IntentResult("faq", 0.9, "rule", ""))
    check(m["skipped_malformed"] == 7 and m["total_samples"] == 1, "7 malformed records skipped and counted; the 1 valid record evaluated")

    m = evaluate_intents(
        [{"text": "x", "expected_intent": "faq"}],
        classify_fn=lambda t: IntentResult("some-unexpected-label", 0.5, "semantic", ""),
    )
    check(m["confusion_matrix"]["faq"]["unknown"] == 1, "a predicted label outside the known set is bucketed as 'unknown' in the confusion matrix, not dropped")


# --- Retrieval metrics --------------------------------------------------------

def test_retrieval_metrics_hand_computed():
    results = [
        EvalResult("q1", "account", [article("account", 0.2, True), article("billing", 0.5, True), article("shipping", 1.4, False)], True),
        EvalResult("q2", "billing", [article("account", 0.3, True), article("billing", 1.2, False)], True),
        EvalResult("q3", "shipping", [article("returns", 1.5, False), article("general", 1.7, False)], False),
        EvalResult("q4", "returns", [], False),
    ]
    m = evaluate_retrieval_results(results)

    check(m["total_queries"] == 4, "4 queries evaluated")
    check(m["topk_hit_rate"] == 0.5 and m["topk_hits"] == 2, "top-K hit rate = 2/4 (q1, q2)")
    check(m["top1_hit_rate"] == 0.25 and m["top1_hits"] == 1, "top-1 hit rate = 1/4 (only q1's first result is the expected category)")
    check(m["relevant_hit_rate"] == 0.25, "post-threshold hit rate = 1/4 (q2's correct article was below the threshold)")
    check(m["avg_results_returned"] == 1.75, "average results returned = (3+2+2+0)/4 = 1.75")
    check(m["avg_relevant_results"] == 0.75, "average results passing the threshold = (2+1+0+0)/4 = 0.75")
    check(m["filtered_out_results"] == 4, "results filtered out by the threshold = 1+1+2+0 = 4")
    check(m["queries_with_no_results"] == 1, "one query returned no results")
    check(m["mean_top1_distance_when_correct"] == 0.2, "mean top-1 distance when correct = 0.2")
    check(m["mean_top1_distance_when_wrong"] == 0.9, "mean top-1 distance when wrong = (0.3+1.5)/2 = 0.9 (empty q4 excluded)")
    check(m["per_category"]["account"] == {"queries": 1, "topk_hits": 1, "top1_hits": 1}, "per-category: account 1/1/1")
    check(m["per_category"]["billing"] == {"queries": 1, "topk_hits": 1, "top1_hits": 0}, "per-category: billing topk hit but not top-1")


def test_retrieval_empty_and_malformed():
    m = evaluate_retrieval_results([])
    check(m["total_queries"] == 0 and m["topk_hit_rate"] is None and m["avg_results_returned"] is None, "empty retrieval results -> rates undefined (None), no crash")

    m = evaluate_retrieval_results([object(), None, "x", EvalResult("q", "account", [article("account", 0.1, True)], True)])
    check(m["skipped_malformed"] == 3 and m["total_queries"] == 1, "3 malformed retrieval records skipped and counted")


# --- Escalation metrics ---------------------------------------------------------

def test_escalation_metrics_hand_computed():
    def scenario(name, expected, expected_reason=None, category="c"):
        return {"name": name, "category": category, "message": name, "intent": "faq", "confidence": 0.7,
                "expected_escalate": expected, "expected_reason": expected_reason}

    scenarios = [
        scenario("s1", True, "explicit_human_request"),
        scenario("s2", True, "frustration_detected"),
        scenario("s3", False),
        scenario("s4", False),
        scenario("s5", True, "low_intent_confidence"),
    ]
    decisions = {
        "s1": Decision(True, "explicit_human_request"),   # correct, right reason
        "s2": Decision(False, "none"),                     # MISSED
        "s3": Decision(True, "frustration_detected"),      # FALSE escalation
        "s4": Decision(False, "none"),                     # correct non-escalation
        "s5": Decision(True, "frustration_detected"),      # correct decision, WRONG reason
    }
    seen_confidences = []

    def fake_escalate(message, intent_result=None, recent_user_messages=None):
        seen_confidences.append(intent_result.confidence)
        return decisions[message]

    m = evaluate_escalation_scenarios(scenarios, escalate_fn=fake_escalate)

    check(m["total_scenarios"] == 5 and m["correct_decisions"] == 3, "5 scenarios, 3 correct decisions")
    check(m["decision_accuracy"] == 0.6, "decision accuracy = 3/5 = 0.6")
    check(m["true_escalations"] == 2 and m["false_escalations"] == 1 and m["missed_escalations"] == 1 and m["correct_non_escalations"] == 1,
          "confusion counts: TP=2, FP=1, FN=1, TN=1")
    check(m["escalations_made"] == 3, "escalations made = TP + FP = 3")
    check(m["escalation_precision"] == 0.6667 and m["escalation_recall"] == 0.6667, "precision = 2/3, recall = 2/3")
    check(m["reasons_checked"] == 2 and m["reason_accuracy_when_correctly_escalated"] == 0.5, "reason accuracy: 1 of 2 correct escalations had the expected reason")
    check(m["false_escalation_cases"] == ["s3"] and m["missed_escalation_cases"] == ["s2"], "false/missed escalation cases identified by name")
    check(m["missed_escalation_details"] == [{"name": "s2", "intent": "faq", "confidence": 0.7}], "missed-escalation details carry the fixed intent and confidence")
    check(len(m["wrong_reason_cases"]) == 1 and m["wrong_reason_cases"][0]["name"] == "s5", "wrong-reason case identified")
    check(all(c == 0.7 for c in seen_confidences), "each scenario's FIXED synthetic confidence was passed to the escalation logic")


def test_escalation_empty_and_malformed():
    m = evaluate_escalation_scenarios([], escalate_fn=lambda *a, **k: Decision(False, "none"))
    check(m["total_scenarios"] == 0 and m["decision_accuracy"] is None and m["escalation_precision"] is None, "empty scenarios -> undefined metrics, no crash")

    bad = [
        None, {"message": "x"}, {"message": "x", "expected_escalate": "yes", "intent": "faq", "confidence": 0.5},
        {"message": "x", "expected_escalate": True, "intent": "faq", "confidence": "high"},
        {"message": "x", "expected_escalate": True, "intent": "faq", "confidence": True},
    ]
    m = evaluate_escalation_scenarios(bad, escalate_fn=lambda *a, **k: Decision(False, "none"))
    check(m["skipped_malformed"] == 5 and m["total_scenarios"] == 0, "5 malformed scenarios (missing fields, wrong types, bool-as-number) skipped and counted")


def test_escalation_precision_recall_are_not_interchangeable():
    """
    Regression guard found by mutation testing: an earlier fixture had
    FP == FN, making precision == recall, so swapping the two formulas went
    unnoticed. These fixtures are deliberately ASYMMETRIC.
    """
    def scenarios_and_decisions(spec):
        scenarios, decisions = [], {}
        for name, expected, actual in spec:
            scenarios.append({"name": name, "category": "c", "message": name, "intent": "faq", "confidence": 0.7,
                              "expected_escalate": expected, "expected_reason": None})
            decisions[name] = Decision(actual, "x" if actual else "none")
        return scenarios, (lambda message, intent_result=None, recent_user_messages=None: decisions[message])

    # A: TP=1, FP=0, FN=2, TN=1  ->  precision 1/1 = 1.0, recall 1/3 = 0.3333
    sc, fn = scenarios_and_decisions([("a1", True, True), ("a2", True, False), ("a3", True, False), ("a4", False, False)])
    m = evaluate_escalation_scenarios(sc, escalate_fn=fn)
    check(m["escalation_precision"] == 1.0, "A: precision = TP/(TP+FP) = 1/1 = 1.0")
    check(m["escalation_recall"] == 0.3333, "A: recall = TP/(TP+FN) = 1/3 = 0.3333")

    # B: TP=1, FP=2, FN=0, TN=0  ->  precision 1/3 = 0.3333, recall 1/1 = 1.0
    sc, fn = scenarios_and_decisions([("b1", True, True), ("b2", False, True), ("b3", False, True)])
    m = evaluate_escalation_scenarios(sc, escalate_fn=fn)
    check(m["escalation_precision"] == 0.3333, "B: precision = 1/3 = 0.3333")
    check(m["escalation_recall"] == 1.0, "B: recall = 1/1 = 1.0")


def test_escalation_with_real_logic():
    by_name = {s["name"]: s for s in ESCALATION_EVALUATION_SCENARIOS}
    chosen = [by_name["explicit_talk_to_human"], by_name["normal_faq"], by_name["frustration_three_signals"]]
    m = evaluate_escalation_scenarios(chosen)  # default = the REAL Day 9 logic
    check(m["total_scenarios"] == 3 and m["correct_decisions"] == 3, "the real Day 9 logic gets these 3 clear-cut scenarios right")
    check(m["reason_accuracy_when_correctly_escalated"] == 1.0, "and reports the expected reasons for the two escalations")


# --- Feedback summary -------------------------------------------------------------

def test_feedback_summary():
    entries = [
        {"rating": "helpful", "comment": None},
        {"rating": "helpful", "comment": ""},
        {"rating": "not_helpful", "comment": "Didn't help\nat all"},
        {"rating": "weird-value"},
        "not-a-dict",
    ]
    s = summarize_feedback(entries)
    check(s["available"] is True and s["total_feedback"] == 4, "4 dict entries counted (the non-dict junk is ignored)")
    check(s["helpful"] == 2 and s["not_helpful"] == 1 and s["invalid_rating_entries"] == 1, "helpful=2, not_helpful=1, invalid=1")
    check(s["helpful_percentage"] == 66.6667, "helpful percentage = 2 of 3 RATED entries (invalid excluded) = 66.6667")
    check(s["comments"] == [{"rating": "not_helpful", "comment": "Didn't help at all"}], "only non-empty comments kept; newlines collapsed")

    for empty in ([], None):
        s = summarize_feedback(empty)
        check(s["available"] is False and s["message"] == NO_FEEDBACK_MESSAGE and s["helpful_percentage"] is None,
              f"no feedback ({empty!r}) -> reports '{NO_FEEDBACK_MESSAGE}' and no percentage")

    s = summarize_feedback([{"rating": "nonsense"}])
    check(s["available"] is True and s["helpful_percentage"] is None, "only-invalid entries -> percentage undefined (0 rated), not 0%")

    s = summarize_feedback([{"rating": "helpful", "comment": "x" * 1000}])
    check(len(s["comments"][0]["comment"]) <= 201, "a very long comment is truncated for display")

    s = summarize_feedback([{"rating": "helpful", "comment": f"c{i}"} for i in range(50)])
    check(len(s["comments"]) == 20, "comment list is capped at 20 entries")


# --- Dataset integrity --------------------------------------------------------------

def test_dataset_integrity():
    m = evaluate_intents(INTENT_EVALUATION_DATASET, classify_fn=lambda t: IntentResult("unknown", 0.0, "none", ""))
    check(m["skipped_malformed"] == 0 and m["total_samples"] == len(INTENT_EVALUATION_DATASET), "every record in the real intent dataset is well-formed")
    check(all(m["per_intent"][label]["support"] >= 10 for label in INTENT_LABELS), "every intent has at least 10 labeled examples")
    texts = [r["text"].lower() for r in INTENT_EVALUATION_DATASET]
    check(len(texts) == len(set(texts)), "no duplicate messages in the intent dataset")

    e = evaluate_escalation_scenarios(ESCALATION_EVALUATION_SCENARIOS, escalate_fn=lambda *a, **k: Decision(False, "none"))
    check(e["skipped_malformed"] == 0 and e["total_scenarios"] == len(ESCALATION_EVALUATION_SCENARIOS), "every escalation scenario is well-formed")
    names = [s["name"] for s in ESCALATION_EVALUATION_SCENARIOS]
    check(len(names) == len(set(names)), "escalation scenario names are unique")
    expectations = {s["expected_escalate"] for s in ESCALATION_EVALUATION_SCENARIOS}
    check(expectations == {True, False}, "scenarios include both should-escalate and should-not-escalate cases")
    check(len(SAMPLE_QUERIES) == 12, "the 12 existing Day 5 sample queries are reused, unchanged")


# --- Runner honesty + report generation ---------------------------------------------

def _perfect_classifier():
    lookup = {r["text"]: r["expected_intent"] for r in INTENT_EVALUATION_DATASET}
    return lambda text: IntentResult(lookup[text], 0.95, "rule", "fake")


def test_runner_unavailable_paths_and_report():
    tmp = tempfile.mkdtemp()
    try:
        missing_db = os.path.join(tmp, "no_such.db")

        def must_not_be_called(*args, **kwargs):
            raise AssertionError("retrieval must NOT be run when it is unavailable")

        results = run_all_evaluations(
            generated_at="2000-01-01T00:00:00+00:00",
            classify_fn=_perfect_classifier(),
            retrieve_fn=must_not_be_called,
            semantic_check_fn=lambda: False,
            retrieval_check_fn=lambda: (False, "test: index not built"),
            feedback_db_path=missing_db,
        )

        check(results["retrieval"]["status"] == "unavailable" and results["retrieval"]["metrics"] is None,
              "unavailable retrieval -> status 'unavailable' and NO metrics at all (nothing fabricated)")
        check(not os.path.exists(missing_db), "checking for feedback did NOT create a database file as a side effect")
        check(results["feedback"]["metrics"]["available"] is False and results["feedback"]["metrics"]["message"] == NO_FEEDBACK_MESSAGE,
              "no feedback database -> 'No production/user feedback data available.'")
        check("PARTIAL" in results["intent"]["label"], "intent results are labeled PARTIAL when the embedding fallback is unavailable")

        md = build_markdown_report(results)
        for heading in ["## 1. Evaluation scope", "## 2. Dataset description", "## 3. Intent metrics", "## 4. Retrieval / relevance metrics",
                        "## 5. Escalation metrics", "## 6. Feedback metrics", "## 7. Limitations", "## 8. Optimization opportunities",
                        "## 9. Conclusion"]:
            check(heading in md, f"report contains section '{heading}'")
        check("UNAVAILABLE" in md and "Not measured" in md, "report clearly states retrieval was not measured")
        check(NO_FEEDBACK_MESSAGE in md, "report states there is no feedback data")
        check("not real-world production performance" in md, "report distinguishes dataset performance from production performance")
        check("This report is INCOMPLETE" in md and "embedding model" in md and "ChromaDB retrieval index" in md,
              "an incomplete-environment report carries a prominent INCOMPLETE notice naming what was missing")

        paths = write_reports(results, os.path.join(tmp, "reports"))
        with open(paths["json"], encoding="utf-8") as f:
            loaded = json.load(f)
        check(loaded == json.loads(json.dumps(results)), "written JSON is valid and round-trips to the exact results")
        check({"meta", "intent", "retrieval", "escalation", "feedback"} <= set(loaded), "JSON contains all evaluation sections")
        try:
            json.dumps(results, allow_nan=False)
            check(True, "results contain no NaN/Infinity (strictly valid JSON)")
        except ValueError:
            check(False, "results contain NaN/Infinity")
        with open(paths["markdown"], encoding="utf-8") as f:
            check(f.read() == md, "the written Markdown file matches the generated report exactly")
    finally:
        shutil.rmtree(tmp, ignore_errors=True)


def test_runner_available_paths():
    lookup = {q["query"]: q["expected_category"] for q in SAMPLE_QUERIES}

    def perfect_retrieve(query, top_k=3):
        return [article(lookup[query], 0.2, True)]

    tmp = tempfile.mkdtemp()
    try:
        db_path = os.path.join(tmp, "conversations.db")
        init_db(db_path=db_path)

        # feedback table exists but is empty -> "no data", not "0%"
        empty = run_all_evaluations(
            classify_fn=_perfect_classifier(), retrieve_fn=perfect_retrieve,
            semantic_check_fn=lambda: True, retrieval_check_fn=lambda: (True, ""), feedback_db_path=db_path,
        )
        check(empty["feedback"]["label"] == "NO DATA — the feedback table is empty", "empty feedback table -> labeled NO DATA")
        check(empty["retrieval"]["status"] == "measured" and empty["retrieval"]["metrics"]["topk_hit_rate"] == 1.0,
              "available retrieval path computes metrics from the (fake, perfect) retriever: top-K hit rate 1.0")
        check("MEASURED" in empty["intent"]["label"] and "PARTIAL" not in empty["intent"]["label"], "intent label is plain MEASURED when the embedding fallback is available")

        # a synthetic feedback row inside a TEMP database (test data only)
        session_id = create_session(db_path=db_path)
        add_message(session_id, "user", "hi", db_path=db_path)
        message_id = add_message(session_id, "assistant", "hello", db_path=db_path)
        submit_feedback(session_id, message_id, "helpful", db_path=db_path)

        with_data = run_all_evaluations(
            classify_fn=_perfect_classifier(), retrieve_fn=perfect_retrieve,
            semantic_check_fn=lambda: True, retrieval_check_fn=lambda: (True, ""), feedback_db_path=db_path,
        )
        check(with_data["feedback"]["metrics"]["helpful"] == 1 and with_data["feedback"]["metrics"]["helpful_percentage"] == 100.0,
              "stored feedback is read and summarized (1 helpful = 100.0%)")

        md = build_markdown_report(with_data)
        check("Top-K hit rate" in md and "100.0%" in md, "measured retrieval appears in the report when available")
        check("INCOMPLETE" not in md, "a fully-available environment's report does NOT carry the INCOMPLETE notice")

        no_table = os.path.join(tmp, "empty.db")
        open(no_table, "w").close()
        check(load_feedback_entries(no_table) is None, "an existing database file with no feedback table is handled (None), not a crash")
    finally:
        shutil.rmtree(tmp, ignore_errors=True)


def test_determinism_and_report_safety():
    kwargs = dict(
        generated_at="2000-01-01T00:00:00+00:00", classify_fn=_perfect_classifier(),
        semantic_check_fn=lambda: False, retrieval_check_fn=lambda: (False, "r"),
        feedback_db_path=os.path.join(tempfile.gettempdir(), "definitely_missing_smartassist.db"),
    )
    a, b = run_all_evaluations(**kwargs), run_all_evaluations(**kwargs)
    check(a == b, "two runs with identical inputs produce identical results")
    check(build_markdown_report(a) == build_markdown_report(b), "the Markdown report is byte-identical across runs")
    check(json.dumps(a, sort_keys=True) == json.dumps(b, sort_keys=True), "the JSON output is identical across runs")

    check(_cell("a|b\nc") == "a\\|b c", "table-cell escaping neutralizes pipes and newlines from untrusted text")

    tricky = [{"text": "x | y", "expected_intent": "faq"}]
    r = run_all_evaluations(
        generated_at="t", classify_fn=lambda t: IntentResult("unknown", 0.0, "none", ""), semantic_check_fn=lambda: False,
        retrieval_check_fn=lambda: (False, "r"), feedback_db_path=kwargs["feedback_db_path"], intent_dataset=tricky,
    )
    check("x \\| y" in build_markdown_report(r), "a message containing '|' cannot break the Markdown table")

    findings = derive_findings(a)
    check(any(f.startswith("Code review, not measured") for f in findings), "code-review observations are explicitly labeled as not measured")
    check(not any("recall" in f for f in findings if f.startswith("Intent")), "a perfect classifier produces no 'low recall' intent findings (findings follow the data)")


def test_findings_follow_measured_data():
    scenarios = [{"name": "live_person", "category": "c", "message": "m", "intent": "escalation", "confidence": 0.9,
                  "expected_escalate": True, "expected_reason": None}]
    r = run_all_evaluations(
        generated_at="t", classify_fn=_perfect_classifier(), escalate_fn=lambda *a, **k: Decision(False, "none"),
        semantic_check_fn=lambda: True, retrieval_check_fn=lambda: (False, "r"),
        feedback_db_path=os.path.join(tempfile.gettempdir(), "definitely_missing_smartassist.db"),
        escalation_scenarios=scenarios,
    )
    findings = derive_findings(r)
    check(any("live_person" in f and "intent label" in f for f in findings), "a missed escalation that carried the 'escalation' intent label produces the intent-label finding")
    check(any("RELEVANCE_DISTANCE_THRESHOLD should NOT be changed" in f for f in findings), "with retrieval unavailable, the report explicitly advises NOT changing the threshold")


def test_intent_finding_splits_unknown_from_confused():
    dataset = [{"text": "m1", "expected_intent": "faq"}, {"text": "m2", "expected_intent": "faq"}, {"text": "m3", "expected_intent": "faq"}]
    answers = {"m1": IntentResult("unknown", 0.0, "none", ""), "m2": IntentResult("complaint", 0.95, "rule", ""), "m3": IntentResult("faq", 0.95, "rule", "")}
    r = run_all_evaluations(
        generated_at="t", classify_fn=lambda text: answers[text], semantic_check_fn=lambda: True,
        retrieval_check_fn=lambda: (False, "r"), intent_dataset=dataset,
        feedback_db_path=os.path.join(tempfile.gettempdir(), "definitely_missing_smartassist.db"),
    )
    faq = [f for f in derive_findings(r) if f.startswith("Intent: 'faq' recall")]
    check(len(faq) == 1 and "recall was 66.7% (2/3)" not in faq[0] and "(1/3)" in faq[0],
          "hand-computed: faq recall = 1/3 (only m3 correct) is reported")
    check("Of its 2 miss(es): 1 predicted 'unknown' (no rule matched), 1 mislabeled as another intent." in faq[0],
          "the 2 misses are split correctly: 1 'unknown' (m1) and 1 confused with another intent (m2)")

    single = {"name": "solo", "category": "c", "message": "m", "intent": "escalation", "confidence": 0.9, "expected_escalate": True, "expected_reason": None}
    r = run_all_evaluations(
        generated_at="t", classify_fn=_perfect_classifier(), escalate_fn=lambda *a, **k: Decision(False, "none"),
        semantic_check_fn=lambda: True, retrieval_check_fn=lambda: (False, "r"),
        feedback_db_path=os.path.join(tempfile.gettempdir(), "definitely_missing_smartassist.db"), escalation_scenarios=[single],
    )
    finding = [f for f in derive_findings(r) if "intent label" in f][0]
    check("yet was not escalated" in finding and "yet were" not in finding, "a single missed case is described in the singular ('was')")


def test_real_run_smoke():
    """Runs the REAL evaluation in whatever environment this is; asserts structure, not environment-specific numbers."""
    kwargs = dict(generated_at="t", feedback_db_path=os.path.join(tempfile.gettempdir(), "definitely_missing_smartassist.db"))
    r1, r2 = run_all_evaluations(**kwargs), run_all_evaluations(**kwargs)
    check(r1["intent"]["metrics"]["total_samples"] == len(INTENT_EVALUATION_DATASET), "real run evaluates every intent sample")
    check(r1["escalation"]["metrics"]["total_scenarios"] == len(ESCALATION_EVALUATION_SCENARIOS), "real run evaluates every escalation scenario")
    check(r1["retrieval"]["status"] in ("measured", "unavailable"), "retrieval is either genuinely measured or honestly unavailable")
    if r1["retrieval"]["status"] == "unavailable":
        check(r1["retrieval"]["metrics"] is None, "if retrieval is unavailable in this environment, no retrieval numbers exist")
    check(r1 == r2, "the real evaluation is reproducible within an environment")


def run():
    tests = [
        test_safe_div,
        test_intent_metrics_hand_computed,
        test_intent_empty_and_malformed,
        test_retrieval_metrics_hand_computed,
        test_retrieval_empty_and_malformed,
        test_escalation_metrics_hand_computed,
        test_escalation_empty_and_malformed,
        test_escalation_precision_recall_are_not_interchangeable,
        test_escalation_with_real_logic,
        test_feedback_summary,
        test_dataset_integrity,
        test_runner_unavailable_paths_and_report,
        test_runner_available_paths,
        test_determinism_and_report_safety,
        test_findings_follow_measured_data,
        test_intent_finding_splits_unknown_from_confused,
        test_real_run_smoke,
    ]
    for test in tests:
        print(f"\n--- {test.__name__} ---")
        test()

    print("\n" + "-" * 60)
    if _failures == 0:
        print("ALL EVALUATION METRICS TESTS PASSED (deterministic fixtures; no real embedding/ChromaDB/LLM used)")
    else:
        print(f"{_failures} TEST(S) FAILED")
        # Exit non-zero on failure so the exit code is a trustworthy signal
        # (found while investigating an unrelated exit code: printing
        # failures alone would still exit 0).
        sys.exit(1)


if __name__ == "__main__":
    run()
