"""
Day 13: turns the results dictionary from app.evaluation_runner into
  - reports/evaluation_results.json   (machine-readable)
  - reports/evaluation_report.md      (human-readable)

Every finding and recommendation in the "Optimization opportunities"
section is DERIVED FROM the measured results passed in (see
derive_findings) — nothing is hard-coded as if it had been measured.
Observations that come from reading the code rather than from a metric
are explicitly labeled "code review, not measured".

The report is deterministic: given the same results dict (including the
same meta.generated_at), it produces byte-identical output.
"""

import json
import os
from typing import List, Optional

from app.evaluation_metrics import INTENT_LABELS


def _pct(value: Optional[float]) -> str:
    return "n/a (no data)" if value is None else f"{value * 100:.1f}%"


def _frac(numerator: int, denominator: int) -> str:
    return f"{numerator}/{denominator}"


def _cell(text) -> str:
    """Makes untrusted text safe for a Markdown table cell."""
    return str(text).replace("|", "\\|").replace("\n", " ").replace("\r", " ")


# --- Findings (derived strictly from measured data) --------------------------

def derive_findings(results: dict) -> List[str]:
    findings: List[str] = []
    meta = results["meta"]

    # Intent
    intent = results["intent"]["metrics"]
    if intent["total_samples"] == 0:
        findings.append("Intent: no valid evaluation samples were available, so no intent findings can be made.")
    else:
        for label, stats in intent["per_intent"].items():
            recall = stats["recall"]
            if stats["support"] and recall is not None and recall < 1.0:
                misses = [m for m in intent["misclassified"] if m["expected"] == label]
                unknown_misses = sum(1 for m in misses if m["predicted"] == "unknown")
                confused_misses = len(misses) - unknown_misses
                findings.append(
                    f"Intent: '{label}' recall was {_pct(recall)} ({_frac(stats['correct'], stats['support'])}) on this dataset. "
                    f"Of its {len(misses)} miss(es): {unknown_misses} predicted 'unknown' (no rule matched), "
                    f"{confused_misses} mislabeled as another intent. See the misclassified examples in section 3."
                )
        rule_stats = intent["method_breakdown"].get("rule")
        if rule_stats and rule_stats["accuracy"] is not None and rule_stats["accuracy"] < 1.0:
            wrong_rule = [m for m in intent["misclassified"] if m["method"] == "rule"]
            findings.append(
                f"Intent: when a keyword rule fired it was wrong {rule_stats['count'] - rule_stats['correct']} "
                f"time(s) out of {rule_stats['count']}: "
                + "; ".join(f"'{m['text']}' expected {m['expected']}, got {m['predicted']}" for m in wrong_rule)
                + ". These point at specific rule phrases or the rule priority order."
            )
        if intent["unknown_predictions"]:
            base = (
                f"Intent: {intent['unknown_predictions']} of {intent['total_samples']} messages ended as 'unknown' "
                "(no rule matched and no confident semantic match)."
            )
            if not meta["semantic_fallback_available"]:
                base += (
                    " The embedding fallback was UNAVAILABLE in this run, so it cannot be judged from these results "
                    "whether it would resolve them — re-run locally before changing any rules or thresholds."
                )
            findings.append(base)

    # Retrieval
    retrieval = results["retrieval"]
    if retrieval["status"] == "unavailable":
        findings.append(
            "Retrieval: no metrics were produced in this environment "
            f"({retrieval['reason']}). No retrieval-based recommendation can be made, and "
            "RELEVANCE_DISTANCE_THRESHOLD should NOT be changed until real distances have been observed by running "
            "`python -m scripts.run_evaluation` with the index built."
        )
    else:
        rm = retrieval["metrics"]
        if rm["total_queries"]:
            if rm["relevant_hit_rate"] is not None and rm["topk_hit_rate"] is not None and rm["relevant_hit_rate"] < rm["topk_hit_rate"]:
                findings.append(
                    f"Retrieval: the expected category was found in the top-K for {_pct(rm['topk_hit_rate'])} of queries but only "
                    f"{_pct(rm['relevant_hit_rate'])} after relevance-threshold filtering, so the current threshold is discarding "
                    "some correct results. Compare 'mean top-1 distance when correct' vs 'when wrong' in section 4 before adjusting it."
                )
            if rm["queries_with_no_results"]:
                findings.append(f"Retrieval: {rm['queries_with_no_results']} query/queries returned no results at all.")

    # Escalation
    esc = results["escalation"]["metrics"]
    if esc["total_scenarios"] == 0:
        findings.append("Escalation: no valid scenarios were available, so no escalation findings can be made.")
    else:
        if esc["missed_escalation_cases"]:
            findings.append(
                "Escalation: missed escalations (should have escalated but did not): "
                + ", ".join(esc["missed_escalation_cases"])
                + ". These are the concrete cases where detection could be improved."
            )
        missed_but_labeled_escalation = [d for d in esc["missed_escalation_details"] if d["intent"] == "escalation"]
        if missed_but_labeled_escalation:
            plural = len(missed_but_labeled_escalation) > 1
            findings.append(
                "Escalation: " + ", ".join(d["name"] for d in missed_but_labeled_escalation)
                + " carried the intent label 'escalation' (at confidence "
                + ", ".join(str(d["confidence"]) for d in missed_but_labeled_escalation)
                + (") yet were" if plural else ") yet was")
                + " not escalated, because the Day 9 explicit-request check only matches its phrase list "
                "and does not consider the intent label. A candidate improvement is to treat an 'escalation' intent at "
                "sufficient confidence as an explicit request; this is supported only by this small synthetic scenario "
                "set, so verify it against real router output before changing production behavior."
            )
        if esc["false_escalation_cases"]:
            findings.append(
                "Escalation: false escalations (escalated but should not have): "
                + ", ".join(esc["false_escalation_cases"]) + "."
            )
        for case in esc["wrong_reason_cases"]:
            findings.append(
                f"Escalation: '{case['name']}' escalated correctly but reported reason '{case['actual_reason']}' "
                f"instead of the expected '{case['expected_reason']}'."
            )

    # Feedback
    fb = results["feedback"]["metrics"]
    if not fb["available"]:
        findings.append(
            "Feedback: " + fb["message"] + " Feedback cannot yet be used to find weak responses — "
            "collect real usage first, then review 'not helpful' entries and their comments."
        )
    elif fb["not_helpful"]:
        findings.append(
            f"Feedback: {fb['not_helpful']} 'not helpful' rating(s) exist — review those responses and any comments "
            "to find weak knowledge-base coverage."
        )

    # Code-review observations — clearly NOT measurements
    findings.append(
        "Code review, not measured: the semantic intent fallback re-embeds all example phrases on every classification "
        "call (see app/intent_router.py, _semantic_match). Caching those example embeddings would likely reduce latency, "
        "but the size of the benefit was not measured."
    )
    findings.append(
        "Code review, not measured: there is currently no caching of LLM responses for repeated queries, although the "
        "project brief lists caching as a way to stay within free-tier API limits."
    )
    findings.append(
        "Not measured: the brief's latency target (under 5 seconds for 90% of queries) — it requires a running server, "
        "the real embedding model, and a live LLM API."
    )
    return findings


# --- Markdown report ---------------------------------------------------------

def build_markdown_report(results: dict) -> str:
    meta = results["meta"]
    intent = results["intent"]["metrics"]
    retrieval = results["retrieval"]
    esc = results["escalation"]["metrics"]
    fb = results["feedback"]["metrics"]

    lines: List[str] = []
    add = lines.append

    add("# SmartAssist — Evaluation Report")
    add("")
    add(f"Generated: {meta['generated_at']}  ")
    add(f"Python: {meta['python_version']}  ")
    add(f"Embedding fallback available in this run: {'yes' if meta['semantic_fallback_available'] else 'NO'}  ")
    add(f"Real retrieval evaluation possible in this run: {'yes' if meta['retrieval_available'] else 'NO'}")
    add("")
    missing = []
    if not meta["semantic_fallback_available"]:
        missing.append("the embedding model (sentence-transformers)")
    if not meta["retrieval_available"]:
        missing.append("the ChromaDB retrieval index")
    if missing:
        add("> **This report is INCOMPLETE.** It was generated in an environment without " + " and without ".join(missing) + ". "
            "Sections marked PARTIAL or UNAVAILABLE were not fully measured. Before using this as the project's evaluation "
            "report, regenerate it on a machine with the requirements installed and the index built: "
            "`python -m scripts.build_index`, then `python -m scripts.run_evaluation`.")
        add("")
    add("**How to read the labels below:** *MEASURED* = computed from real code on the stated dataset. "
        "*PARTIAL* = measured, but with a component unavailable. *UNAVAILABLE / NO DATA* = not measured; no numbers "
        "were invented. Synthetic/hand-written datasets are always labeled as such.")
    add("")

    add("## 1. Evaluation scope")
    add("")
    add("This report evaluates four components **individually**: the Day 7 intent router, Day 5 retrieval, "
        "Day 9 escalation logic, and Day 12 feedback data. It is a **component-level** evaluation. "
        "`POST /chat` currently echoes messages and does not yet run the intent → retrieval → LLM → escalation "
        "pipeline, so end-to-end answer quality, hallucination avoidance, and latency are **not measured** here.")
    add("")

    add("## 2. Dataset description")
    add("")
    add(f"- **Intent dataset:** {intent['total_samples']} hand-written labeled messages "
        f"({intent['skipped_malformed']} malformed records skipped) covering greeting, faq, complaint, "
        "technical_issue, and escalation, plus a few mixed messages. Written for this project — not real customer data.")
    add(f"- **Retrieval queries:** the {results['retrieval_sample_query_count']} Day 5 sample queries "
        "(2 per knowledge-base category), labeled with an expected *category*.")
    add(f"- **Escalation scenarios:** {esc['total_scenarios']} synthetic scenarios with FIXED intent confidences "
        f"({esc['skipped_malformed']} malformed skipped). They test the decision logic, not real traffic.")
    add("- **Feedback:** real stored feedback only; nothing synthetic is used in this report.")
    add("")
    add("> All datasets are small and hand-written. Results describe performance on **these datasets**, "
        "not real-world production performance.")
    add("")

    add("## 3. Intent metrics")
    add("")
    add(f"**Status:** {results['intent']['label']}")
    add("")
    if intent["total_samples"] == 0:
        add("No valid samples — no metrics computed.")
    else:
        add(f"- Total samples: {intent['total_samples']}")
        add(f"- Correct: {intent['correct']}  |  Incorrect: {intent['incorrect']}")
        add(f"- **Accuracy: {_pct(intent['accuracy'])}** ({_frac(intent['correct'], intent['total_samples'])})")
        add(f"- Predicted 'unknown': {intent['unknown_predictions']}")
        add(f"- Mean confidence when correct: {intent['mean_confidence_correct']}  |  when incorrect: {intent['mean_confidence_incorrect']}")
        add("")
        add("| Intent | Support | Correct | Precision | Recall |")
        add("|---|---|---|---|---|")
        for label in INTENT_LABELS:
            s = intent["per_intent"][label]
            add(f"| {label} | {s['support']} | {s['correct']} | {_pct(s['precision'])} | {_pct(s['recall'])} |")
        add("")
        add("**Confusion matrix** (rows = expected, columns = predicted):")
        add("")
        columns = INTENT_LABELS + ["unknown"]
        add("| expected \\ predicted | " + " | ".join(columns) + " |")
        add("|---|" + "---|" * len(columns))
        for label in INTENT_LABELS:
            add(f"| {label} | " + " | ".join(str(intent["confusion_matrix"][label][c]) for c in columns) + " |")
        add("")
        add("**By detection method:**")
        add("")
        add("| Method | Count | Correct | Accuracy |")
        add("|---|---|---|---|")
        for method, s in intent["method_breakdown"].items():
            add(f"| {method} | {s['count']} | {s['correct']} | {_pct(s['accuracy'])} |")
        add("")
        if intent["misclassified"]:
            add("**Misclassified examples:**")
            add("")
            add("| Message | Expected | Predicted | Confidence | Method |")
            add("|---|---|---|---|---|")
            for m in intent["misclassified"]:
                add(f"| {_cell(m['text'])} | {m['expected']} | {m['predicted']} | {m['confidence']} | {m['method']} |")
        else:
            add("No misclassified examples.")
    add("")

    add("## 4. Retrieval / relevance metrics")
    add("")
    add(f"**Status:** {retrieval['label']}")
    add("")
    if retrieval["status"] == "unavailable":
        add(f"**Not measured.** Reason: {retrieval['reason']}.")
        add("")
        add("The evaluation framework is implemented and unit-tested with deterministic fixtures "
            "(`tests/test_evaluation_metrics.py`), but real retrieval numbers require the real embedding model and a "
            "built ChromaDB index. Run `python -m scripts.build_index` and then `python -m scripts.run_evaluation` locally.")
    else:
        rm = retrieval["metrics"]
        add(f"- Queries: {rm['total_queries']}  (top-K = {meta['retrieval_top_k']}, distance threshold = {meta['relevance_distance_threshold']})")
        add(f"- Top-K hit rate (expected category anywhere in results): {_pct(rm['topk_hit_rate'])} ({_frac(rm['topk_hits'], rm['total_queries'])})")
        add(f"- Top-1 hit rate: {_pct(rm['top1_hit_rate'])} ({_frac(rm['top1_hits'], rm['total_queries'])})")
        add(f"- Hit rate after relevance-threshold filtering: {_pct(rm['relevant_hit_rate'])} ({_frac(rm['relevant_hits'], rm['total_queries'])})")
        add(f"- Average results returned: {rm['avg_results_returned']}  |  average passing the threshold: {rm['avg_relevant_results']}")
        add(f"- Results filtered out by the threshold: {rm['filtered_out_results']}")
        add(f"- Queries with no results: {rm['queries_with_no_results']}")
        add(f"- Mean top-1 distance when correct: {rm['mean_top1_distance_when_correct']}  |  when wrong: {rm['mean_top1_distance_when_wrong']}")
        add("")
        add("| Expected category | Queries | Top-K hits | Top-1 hits |")
        add("|---|---|---|---|")
        for category, s in sorted(rm["per_category"].items()):
            add(f"| {category} | {s['queries']} | {s['topk_hits']} | {s['top1_hits']} |")
    add("")

    add("## 5. Escalation metrics")
    add("")
    add(f"**Status:** {results['escalation']['label']}")
    add("")
    if esc["total_scenarios"] == 0:
        add("No valid scenarios — no metrics computed.")
    else:
        add(f"- Scenarios: {esc['total_scenarios']}")
        add(f"- **Decision accuracy: {_pct(esc['decision_accuracy'])}** ({_frac(esc['correct_decisions'], esc['total_scenarios'])})")
        add(f"- Escalations made: {esc['escalations_made']}  (true: {esc['true_escalations']}, false: {esc['false_escalations']})")
        add(f"- Missed escalations: {esc['missed_escalations']}  |  Correct non-escalations: {esc['correct_non_escalations']}")
        add(f"- Escalation precision: {_pct(esc['escalation_precision'])}  |  recall: {_pct(esc['escalation_recall'])}")
        add(f"- Reason matched expectation (among correct escalations with an expected reason): "
            f"{_pct(esc['reason_accuracy_when_correctly_escalated'])} ({esc['reasons_checked']} checked)")
        add("")
        add("| Scenario category | Scenarios | Correct |")
        add("|---|---|---|")
        for category, s in sorted(esc["per_category"].items()):
            add(f"| {category} | {s['scenarios']} | {s['correct']} |")
        add("")
        add("- False escalation cases: " + (", ".join(esc["false_escalation_cases"]) or "none"))
        add("- Missed escalation cases: " + (", ".join(esc["missed_escalation_cases"]) or "none"))
    add("")

    add("## 6. Feedback metrics")
    add("")
    add(f"**Status:** {results['feedback']['label']}")
    add("")
    if not fb["available"]:
        add(fb["message"])
    else:
        add(f"- Total feedback: {fb['total_feedback']}")
        add(f"- Helpful: {fb['helpful']}  |  Not helpful: {fb['not_helpful']}  |  Unrecognized rating values: {fb['invalid_rating_entries']}")
        add(f"- Helpful percentage: {_pct(None if fb['helpful_percentage'] is None else fb['helpful_percentage'] / 100)}")
        if fb["comments"]:
            add("")
            add("Comments (shown as stored, for human reading; no sentiment conclusions are drawn):")
            add("")
            for c in fb["comments"]:
                add(f"- [{c['rating']}] {_cell(c['comment'])}")
    add("")

    add("## 7. Limitations")
    add("")
    add("- All datasets are small and hand-written; they are not real customer traffic.")
    add("- Component-level only: the live `/chat` endpoint does not yet run the full pipeline, so end-to-end quality is unmeasured.")
    add("- Retrieval is evaluated at category level, not at individual-article level.")
    add("- Escalation scenarios use fixed synthetic intent confidences; real confidences depend on the embedding model.")
    add("- Latency, LLM answer quality, and hallucination rate were not measured.")
    add("- Feedback metrics reflect only users who chose to rate a response.")
    add("")

    add("## 8. Optimization opportunities")
    add("")
    add("Each item below is derived from the measurements above or explicitly labeled as an unmeasured code-review observation. "
        "No thresholds were changed to improve any metric.")
    add("")
    for finding in derive_findings(results):
        add(f"- {finding}")
    add("")

    add("## 9. Conclusion (based strictly on measured results)")
    add("")
    if intent["total_samples"]:
        add(f"- On the hand-written intent dataset, accuracy was {_pct(intent['accuracy'])} "
            f"({'full system' if meta['semantic_fallback_available'] else 'rule layer only — embedding fallback unavailable'}).")
    if retrieval["status"] == "measured":
        add(f"- On the Day 5 sample queries, the expected category appeared in the top-K for {_pct(retrieval['metrics']['topk_hit_rate'])} of queries.")
    else:
        add("- Retrieval quality was not measured in this environment; no claim is made about it.")
    if esc["total_scenarios"]:
        add(f"- On the synthetic escalation scenarios, decision accuracy was {_pct(esc['decision_accuracy'])} "
            f"with {esc['false_escalations']} false and {esc['missed_escalations']} missed escalation(s).")
    add("- " + (fb["message"] if not fb["available"] else f"Feedback: {fb['helpful']} helpful vs {fb['not_helpful']} not helpful.")
        + ("" if fb["available"] else " No conclusion about user satisfaction can be drawn."))
    add("")
    return "\n".join(lines)


def write_reports(results: dict, output_dir: str) -> dict:
    """Writes evaluation_results.json and evaluation_report.md into output_dir; returns their paths."""
    os.makedirs(output_dir, exist_ok=True)
    json_path = os.path.join(output_dir, "evaluation_results.json")
    md_path = os.path.join(output_dir, "evaluation_report.md")

    with open(json_path, "w", encoding="utf-8") as f:
        json.dump(results, f, indent=2, sort_keys=True, ensure_ascii=False)
        f.write("\n")
    with open(md_path, "w", encoding="utf-8") as f:
        f.write(build_markdown_report(results))

    return {"json": json_path, "markdown": md_path}
