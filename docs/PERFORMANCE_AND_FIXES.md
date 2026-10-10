# SmartAssist: escalation fix and performance work

## Root causes of the repeated "connect you with a member of our support team" reply

1. **Low classifier confidence escalated on its own** (`app/escalation.py`, `app/config.py`).
   Any message that did not contain a hard-coded rule phrase fell through to the semantic
   classifier, which scores ~0.3-0.5 for natural phrasing ("I forgot my password", "my order is
   late"). Anything under `INTENT_ESCALATION_THRESHOLD` (0.6) escalated. If the embedding model was
   unavailable or still loading, confidence was `0.0`, so *everything* escalated. Only messages
   containing phrases like "how do i" or "where is my" (confidence 0.95) got through.
   **Fix:** low confidence alone no longer escalates (it means "general question"). Explicit human
   requests and genuine frustration still escalate. Legacy behaviour: `ESCALATE_ON_LOW_CONFIDENCE=true`.
2. **The Gemini model was retired.** `gemini-1.5-flash` returns "model not found", and the
   `google-generativeai` SDK is deprecated. Even non-escalated questions could not be answered.
   **Fix:** `google-genai` SDK, default `gemini-2.5-flash` (override with `GEMINI_MODEL`).
3. **Retrieval had nothing to retrieve.** `chroma_db/` is in `.dockerignore` and nothing built the
   index at startup, so a fresh container had an empty vector store. **Fix:** the index is baked into
   the image at build time, with a startup self-heal if it is missing.
4. **No knowledge match meant no LLM call.** The old code returned a canned "I don't have information"
   without asking Gemini. **Fix:** Gemini is still called (with a prompt that forbids inventing
   company policy) so general questions are answered.
5. **A greeting word swallowed real questions.** "Hello, how do I reset my password?" matched the
   greeting rule and got "Hello! How can I help?". **Fix:** the scripted greeting is used only for pure greetings.
6. **The escalation reply claimed a handoff that does not exist.** There is no live-agent integration.
   **Fix:** honest wording, plus `handoff_confirmed: false` in the response. The KB article that
   promised "I'll flag your conversation" was corrected.

## Bottlenecks found (by code inspection and counting)

| Per-request work (message with no rule phrase) | Before | After |
|---|---|---|
| Embedding calls / texts embedded | 7 calls / 19 texts | 1 call / 1 text |
| Vector-store client opened | every request | once per process |
| spaCy preprocessing (output unused) | every request | off (opt-in) |
| Gemini client / model object | rebuilt every call | one shared client |
| Embedding model load | first customer request | background warm-up at startup |
| Model download, index build | runtime | image build time |
| Gemini "thinking" (2.5 Flash default) | n/a | disabled (`GEMINI_THINKING_BUDGET=0`) |
| Gemini retries | none (one 10s attempt) | max 1, transient 429/5xx only, never on timeout |

Measured with `python -m scripts.benchmark_pipeline` (identical fakes on both versions): the counts above.
Local wall time per request with zero-cost fakes was unchanged (~4 ms, mostly SQLite). The real saving
comes from model and Chroma work that the fakes do not simulate.

## NOT measured (needs your environment)

Real Gemini latency, real MiniLM inference time, real Chroma query time, Railway cold-start time, and
end-to-end time from a phone. Measure with `scripts/smoke_test_gemini.py` and
`scripts/measure_live_latency.py <your-url>`, and read the `chat_timing` JSON lines in Railway logs.

## Model trade-offs

`gemini-2.5-flash`: better answer quality, a little slower. `gemini-2.5-flash-lite`: faster/cheaper, lower
quality on complex questions. Neither is verified against your key here. Compare with
`GEMINI_MODEL=gemini-2.5-flash-lite python -m scripts.smoke_test_gemini`.

## New configuration (all optional)

`GEMINI_MODEL`, `LLM_TIMEOUT_SECONDS` (12), `LLM_MAX_RETRIES` (1), `LLM_MAX_OUTPUT_TOKENS` (400),
`GEMINI_THINKING_BUDGET` (0), `ESCALATE_ON_LOW_CONFIDENCE`, `RUN_SPACY_PREPROCESSING`,
`WARMUP_ON_STARTUP`, `PERF_LOGGING`, `DATA_DIR` (put SQLite files on a Railway Volume).

## API changes (additive only)

`/chat` now also returns `handoff_confirmed`, `fallback_reason`, `timings_ms`, and sets the
`X-Process-Time-Ms` and `Server-Timing` headers. A concurrent duplicate request for the same session
returns HTTP 429. `/app/`, `/chat`, `/history`, `/feedback`, `/docs` and the admin routes are unchanged.
