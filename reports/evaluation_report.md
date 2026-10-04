# SmartAssist — Evaluation Report

Generated: 2026-10-04T08:43:21+00:00  
Python: 3.12.3  
Embedding fallback available in this run: NO  
Real retrieval evaluation possible in this run: NO

> **This report is INCOMPLETE.** It was generated in an environment without the embedding model (sentence-transformers) and without the ChromaDB retrieval index. Sections marked PARTIAL or UNAVAILABLE were not fully measured. Before using this as the project's evaluation report, regenerate it on a machine with the requirements installed and the index built: `python -m scripts.build_index`, then `python -m scripts.run_evaluation`.

**How to read the labels below:** *MEASURED* = computed from real code on the stated dataset. *PARTIAL* = measured, but with a component unavailable. *UNAVAILABLE / NO DATA* = not measured; no numbers were invented. Synthetic/hand-written datasets are always labeled as such.

## 1. Evaluation scope

This report evaluates four components **individually**: the Day 7 intent router, Day 5 retrieval, Day 9 escalation logic, and Day 12 feedback data. It is a **component-level** evaluation. `POST /chat` currently echoes messages and does not yet run the intent → retrieval → LLM → escalation pipeline, so end-to-end answer quality, hallucination avoidance, and latency are **not measured** here.

## 2. Dataset description

- **Intent dataset:** 52 hand-written labeled messages (0 malformed records skipped) covering greeting, faq, complaint, technical_issue, and escalation, plus a few mixed messages. Written for this project — not real customer data.
- **Retrieval queries:** the 12 Day 5 sample queries (2 per knowledge-base category), labeled with an expected *category*.
- **Escalation scenarios:** 20 synthetic scenarios with FIXED intent confidences (0 malformed skipped). They test the decision logic, not real traffic.
- **Feedback:** real stored feedback only; nothing synthetic is used in this report.

> All datasets are small and hand-written. Results describe performance on **these datasets**, not real-world production performance.

## 3. Intent metrics

**Status:** PARTIAL — measured on the hand-written evaluation dataset with the RULE LAYER ONLY. The embedding fallback was unavailable in this environment, so messages no rule matched became 'unknown'. Re-run in an environment with sentence-transformers for full-system numbers.

- Total samples: 52
- Correct: 43  |  Incorrect: 9
- **Accuracy: 82.7%** (43/52)
- Predicted 'unknown': 9
- Mean confidence when correct: 0.95  |  when incorrect: 0.0

| Intent | Support | Correct | Precision | Recall |
|---|---|---|---|---|
| greeting | 10 | 10 | 100.0% | 100.0% |
| faq | 10 | 7 | 100.0% | 70.0% |
| complaint | 10 | 8 | 100.0% | 80.0% |
| technical_issue | 11 | 9 | 100.0% | 81.8% |
| escalation | 11 | 9 | 100.0% | 81.8% |

**Confusion matrix** (rows = expected, columns = predicted):

| expected \ predicted | greeting | faq | complaint | technical_issue | escalation | unknown |
|---|---|---|---|---|---|---|
| greeting | 10 | 0 | 0 | 0 | 0 | 0 |
| faq | 0 | 7 | 0 | 0 | 0 | 3 |
| complaint | 0 | 0 | 8 | 0 | 0 | 2 |
| technical_issue | 0 | 0 | 0 | 9 | 0 | 2 |
| escalation | 0 | 0 | 0 | 0 | 9 | 2 |

**By detection method:**

| Method | Count | Correct | Accuracy |
|---|---|---|---|
| none | 9 | 0 | 0.0% |
| rule | 43 | 43 | 100.0% |

**Misclassified examples:**

| Message | Expected | Predicted | Confidence | Method |
|---|---|---|---|---|
| What payment methods do you accept? | faq | unknown | 0.0 | none |
| Which browsers do you support? | faq | unknown | 0.0 | none |
| Is there a mobile app? | faq | unknown | 0.0 | none |
| Your support has been terrible | complaint | unknown | 0.0 | none |
| I expected much better from your company | complaint | unknown | 0.0 | none |
| The upload button does nothing | technical_issue | unknown | 0.0 | none |
| I keep getting logged out every few minutes | technical_issue | unknown | 0.0 | none |
| Is there a live person I can chat with? | escalation | unknown | 0.0 | none |
| Transfer me to customer support staff | escalation | unknown | 0.0 | none |

## 4. Retrieval / relevance metrics

**Status:** UNAVAILABLE in this environment — no retrieval metrics were produced

**Not measured.** Reason: vector store unavailable (RuntimeError: chromadb is not installed. Run: pip install chromadb).

The evaluation framework is implemented and unit-tested with deterministic fixtures (`tests/test_evaluation_metrics.py`), but real retrieval numbers require the real embedding model and a built ChromaDB index. Run `python -m scripts.build_index` and then `python -m scripts.run_evaluation` locally.

## 5. Escalation metrics

**Status:** MEASURED on synthetic scenarios with FIXED intent confidences (tests decision logic, not real traffic)

- Scenarios: 20
- **Decision accuracy: 85.0%** (17/20)
- Escalations made: 11  (true: 11, false: 0)
- Missed escalations: 3  |  Correct non-escalations: 6
- Escalation precision: 100.0%  |  recall: 78.6%
- Reason matched expectation (among correct escalations with an expected reason): 100.0% (11 checked)

| Scenario category | Scenarios | Correct |
|---|---|---|
| explicit_request | 4 | 3 |
| frustration | 3 | 3 |
| implicit_frustration | 2 | 0 |
| low_confidence | 3 | 3 |
| normal | 6 | 6 |
| repeated_failure | 2 | 2 |

- False escalation cases: none
- Missed escalation cases: explicit_paraphrase_live_person, implicit_frustration_repeat_asking, implicit_frustration_giving_up

## 6. Feedback metrics

**Status:** NO DATA — no feedback database or feedback table found

No production/user feedback data available.

## 7. Limitations

- All datasets are small and hand-written; they are not real customer traffic.
- Component-level only: the live `/chat` endpoint does not yet run the full pipeline, so end-to-end quality is unmeasured.
- Retrieval is evaluated at category level, not at individual-article level.
- Escalation scenarios use fixed synthetic intent confidences; real confidences depend on the embedding model.
- Latency, LLM answer quality, and hallucination rate were not measured.
- Feedback metrics reflect only users who chose to rate a response.

## 8. Optimization opportunities

Each item below is derived from the measurements above or explicitly labeled as an unmeasured code-review observation. No thresholds were changed to improve any metric.

- Intent: 'faq' recall was 70.0% (7/10) on this dataset. Of its 3 miss(es): 3 predicted 'unknown' (no rule matched), 0 mislabeled as another intent. See the misclassified examples in section 3.
- Intent: 'complaint' recall was 80.0% (8/10) on this dataset. Of its 2 miss(es): 2 predicted 'unknown' (no rule matched), 0 mislabeled as another intent. See the misclassified examples in section 3.
- Intent: 'technical_issue' recall was 81.8% (9/11) on this dataset. Of its 2 miss(es): 2 predicted 'unknown' (no rule matched), 0 mislabeled as another intent. See the misclassified examples in section 3.
- Intent: 'escalation' recall was 81.8% (9/11) on this dataset. Of its 2 miss(es): 2 predicted 'unknown' (no rule matched), 0 mislabeled as another intent. See the misclassified examples in section 3.
- Intent: 9 of 52 messages ended as 'unknown' (no rule matched and no confident semantic match). The embedding fallback was UNAVAILABLE in this run, so it cannot be judged from these results whether it would resolve them — re-run locally before changing any rules or thresholds.
- Retrieval: no metrics were produced in this environment (vector store unavailable (RuntimeError: chromadb is not installed. Run: pip install chromadb)). No retrieval-based recommendation can be made, and RELEVANCE_DISTANCE_THRESHOLD should NOT be changed until real distances have been observed by running `python -m scripts.run_evaluation` with the index built.
- Escalation: missed escalations (should have escalated but did not): explicit_paraphrase_live_person, implicit_frustration_repeat_asking, implicit_frustration_giving_up. These are the concrete cases where detection could be improved.
- Escalation: explicit_paraphrase_live_person carried the intent label 'escalation' (at confidence 0.9) yet was not escalated, because the Day 9 explicit-request check only matches its phrase list and does not consider the intent label. A candidate improvement is to treat an 'escalation' intent at sufficient confidence as an explicit request; this is supported only by this small synthetic scenario set, so verify it against real router output before changing production behavior.
- Feedback: No production/user feedback data available. Feedback cannot yet be used to find weak responses — collect real usage first, then review 'not helpful' entries and their comments.
- Code review, not measured: the semantic intent fallback re-embeds all example phrases on every classification call (see app/intent_router.py, _semantic_match). Caching those example embeddings would likely reduce latency, but the size of the benefit was not measured.
- Code review, not measured: there is currently no caching of LLM responses for repeated queries, although the project brief lists caching as a way to stay within free-tier API limits.
- Not measured: the brief's latency target (under 5 seconds for 90% of queries) — it requires a running server, the real embedding model, and a live LLM API.

## 9. Conclusion (based strictly on measured results)

- On the hand-written intent dataset, accuracy was 82.7% (rule layer only — embedding fallback unavailable).
- Retrieval quality was not measured in this environment; no claim is made about it.
- On the synthetic escalation scenarios, decision accuracy was 85.0% with 0 false and 3 missed escalation(s).
- No production/user feedback data available. No conclusion about user satisfaction can be drawn.
