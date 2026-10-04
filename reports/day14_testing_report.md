# SmartAssist — Day 14 Testing Report

## 1. Objective

Day 14's official scope is "15+ unit/integration tests, edge cases, API
mocking." The project already had far more than 15 tests across Days
1–13 (see section 2). So Day 14's real job was not to hit a number — it
was to **audit** the existing coverage for genuine gaps, close the
meaningful ones, mock the external LLM boundary properly, and find and
fix real bugs, not just add tests that pass trivially.

## 2. Testing strategy

1. **Audit first.** Read every application module and every existing
   test file before writing anything, to avoid duplicating coverage
   that already exists (e.g. Day 9's `test_escalation.py` already has 30
   real checks; Day 12's `test_feedback.py` already has 25).
2. **Find real gaps**, not superficial ones — e.g. "does this function
   exist" tests were explicitly avoided.
3. **Reproduce before fixing.** Every bug below was reproduced with a
   standalone script showing the actual crash/wrong behavior *before*
   any code was changed.
4. **Root-cause fixes**, not test-hiding. No test was weakened or
   deleted to make a failure disappear (see section 6 for the one case
   where a *test* itself was wrong, and how that was verified).
5. **Mock at the right boundary.** For the LLM provider, mock the
   external SDK (`google.generativeai`) via `sys.modules` injection
   rather than only mocking the internal `LLMProvider` interface — this
   exercises `GeminiProvider`'s own code (error classification, request
   construction), which no earlier test had ever actually run.

## 3. Existing coverage audited (Days 1–13)

| Area | Existing file | Real checks |
|---|---|---|
| Preprocessing | `test_preprocessing.py` | 0 executable (spaCy unavailable) |
| KB loader | `test_knowledge_base_loader.py` | 4 |
| Vector store / indexing | `test_vector_store_logic.py` | 4 |
| Retrieval | `test_retrieval_logic.py` | 6 |
| Retrieval evaluation | `test_evaluation_logic.py` | 3 |
| LLM provider (interface-level) | `test_llm_provider.py` | 2 |
| Response generation | `test_response_generator.py` | 10 |
| Intent routing | `test_intent_router.py` | 15 |
| Conversation memory | `test_conversation_memory.py` | 33 |
| Escalation | `test_escalation.py` | 30 |
| Frontend (static) | `test_frontend.py` | 30 |
| Admin | `test_admin.py` | 41 |
| Feedback | `test_feedback.py` | 25 |
| Evaluation metrics (Day 13) | `test_evaluation_metrics.py` | 118 |

This is why Day 14 did not add "15 tests to reach the minimum" — the
project was already at 321 before Day 14 started. Day 14 instead closed
specific, identified gaps.

## 4. Gaps identified and closed

| Gap | New file | Why it was a real gap |
|---|---|---|
| `GeminiProvider`'s own code (SDK calls, error classification) never executed by any test | `test_llm_provider_mocking.py` | Existing LLM tests mock the `LLMProvider` *interface*; `GeminiProvider.generate()`'s internal `genai.configure`/`GenerativeModel`/timeout-rate-limit classification logic had 0% execution |
| Provider exceptions outside the documented `LLMError` hierarchy | `test_response_generator_hardening.py` | Found a real bug (§5.1) |
| Malformed provider success return values (None/empty/non-string) | `test_response_generator_hardening.py` | Found a real bug (§5.2) |
| `retrieved_articles=None` (vs. `[]`) | `test_response_generator_hardening.py` | Found a real bug (§5.3) |
| A single corrupted/unreadable KB file | `test_knowledge_base_edge_cases.py` | Found a real bug (§5.4) |
| KB loader edge cases: no frontmatter, partial frontmatter, malformed lines, empty file, directory matching the glob, unicode | `test_knowledge_base_edge_cases.py` | Previously only tested against the real, well-formed 31-article set |
| Malformed/mismatched-length vector-store responses, `None` metadata, missing response keys, unicode/HTML/very long queries | `test_retrieval_edge_cases.py` | Previously untested shapes |
| Full-stack integration (chat→history→feedback, admin login→KB write→reindex→audit) at the function level | `test_integration_flows.py` | No test previously chained these modules together in one flow |
| Cross-cutting adversarial input (SQL-injection-style session IDs, HTML/script-like message content, intent case-sensitivity, combined escalation signals via the real top-level function, feedback rating case-sensitivity) | `test_integration_flows.py` | Previously tested per-module, not across the seams |

## 5. Bugs found and fixed

Each bug below was reproduced with a real, standalone script **before**
any code was changed, to confirm it was genuine and not a
misunderstanding.

### 5.1 — Provider exceptions outside `LLMError` crashed the whole pipeline
**File:** `app/response_generator.py`
**Repro:**
```python
class BrokenProvider(LLMProvider):
    def generate(self, system_prompt, user_prompt):
        raise ValueError("unexpected SDK error")
generate_response("q", [article], provider=BrokenProvider())
# -> CRASHED: ValueError (uncaught)
```
**Root cause:** `except LLMError:` only caught the documented error
hierarchy. A third-party SDK bug, or a future provider that doesn't
perfectly honor the `LLMError` contract, would crash the entire
response instead of degrading to the safe fallback.
**Fix:** broadened to `except Exception:`.
**Regression test:** `test_response_generator_hardening.py::run` (Bug 1
section, 2 checks).

### 5.2 — Malformed *successful* provider responses were passed straight through
**Repro:** a provider returning `None`, `"   "`, or a `dict` instead of
raising `EmptyResponseError` — no exception, so the old code returned
the malformed value as if it were a valid reply.
**Fix:** added an explicit `isinstance(reply, str) and reply.strip()`
check after the call; anything else is treated as a malformed response
and degrades to the same fallback.
**Regression test:** Bug 2 section, 3 checks (None, whitespace, wrong
type) plus a control case proving a well-behaved provider is
unaffected.

### 5.3 — `generate_response(query, None)` crashed with `TypeError`
**Repro:** `generate_response("q", None)` → `TypeError: 'NoneType'
object is not iterable`. The function already defensively normalized
`user_query` (`(user_query or "").strip()`) but not
`retrieved_articles`.
**Fix:** `retrieved_articles = retrieved_articles or []`, matching the
existing defensive pattern already used for `user_query` in the same
function.
**Regression test:** Bug 3 section, 2 checks (confirms `None` and `[]`
now produce identical, correct fallback behavior).

### 5.4 — One corrupted file crashed the ENTIRE knowledge base load
**File:** `app/knowledge_base_loader.py`
**Repro:**
```python
# account/good1.md   -> valid
# account/corrupted.md -> contains b'\xff\xfe' (invalid UTF-8)
# account/good2.md   -> valid
load_articles(kb_dir=tmp)
# -> CRASHED: UnicodeDecodeError: 'utf-8' codec can't decode byte 0xff...
```
This meant a single bad file anywhere in `knowledge_base/` would return
**zero** articles for retrieval, the admin KB view, and evaluation —
site-wide outage from one file.
**Root cause:** `open(filepath, encoding="utf-8").read()` had no error
handling; one raised exception aborted the whole `for` loop, discarding
every already-parsed article.
**Fix:** each file is now read inside its own `try/except (OSError,
UnicodeDecodeError)`; an unreadable file is skipped (default
`skip_unreadable=True`) so every other article still loads. An opt-in
`skip_unreadable=False` restores the old fail-loudly behavior for
scripts that want to know immediately about a corrupted file.
**Regression test:** `test_knowledge_base_edge_cases.py::test_corrupted_non_utf8_file_does_not_crash_the_whole_load`
(3 checks) plus a related test confirming a directory that happens to
match the `*.md` glob pattern (`IsADirectoryError`, a subclass of
`OSError`) is caught by the same fix.

## 6. A test bug found and corrected (not an application bug)

While writing the admin KB integration test, an assertion failed:
`edited.content == "Updated content."`. Before assuming the application
was broken, the actual file on disk was inspected directly:
```
---
id: testing-how-do-i-integration-test
category: testing
title: "How do I integration test?"
---

# How do I integration test?

Updated content.
```
The edit *was* persisted correctly. `kb_admin.get_article()` always
returns content that includes the `# {title}` heading
`_write_article_file` prepends — this is the same, already-correct
behavior Day 11's own `test_admin.py` accounts for with a substring
check (`"Updated instructions" in edited.content`), not an exact-equality
check. The new test's assertion was simply wrong. It was corrected to
`"Updated content." in reloaded.content`, matching the established,
correct pattern — the application code was not touched for this one.

## 7. API mocking (LLM provider boundary)

`tests/test_llm_provider_mocking.py` injects a fake `google.generativeai`
module into `sys.modules` (since `llm_provider.py` imports it lazily,
inside methods) so `GeminiProvider`'s own real code runs, with no
network access and no real package installed:

- successful response (verifies `configure()` args, `GenerativeModel`
  construction, system/user prompt separation, and the request timeout
  actually reaching the fake SDK call)
- timeout classification (message-based, via a real raised exception)
- rate-limit classification (3 different real-world message shapes)
- generic SDK error → `LLMAPIError`, explicitly confirmed NOT
  misclassified as timeout/rate-limit
- malformed empty response shapes (`""`, `"   "`, `None`) → `EmptyResponseError`
- a real `ConnectionError` (network-style exception) → wrapped as
  `LLMAPIError`, never propagates raw
- two full end-to-end runs through `response_generator.generate_response()`
  with the fake SDK — one success, one failure — proving the mock
  connects all the way through the real pipeline

**No real API key, network call, or paid request was ever made.**

## 8. Edge cases & security-oriented tests

Explicitly tested (see the listed files for exact assertions):
empty/whitespace/very long input; unusual Unicode and emoji; malformed
KB articles (no frontmatter, partial frontmatter, malformed lines, empty
file, corrupted encoding, a directory matching the glob); invalid
article identifiers; path-traversal attempts (category names like
`../../etc`, `billing/../../escape`); malformed/mismatched-length
retrieval results and `None` metadata; invalid/missing/malicious session
IDs (SQL-injection-style strings, verified via parameterized queries);
feedback against nonexistent/cross-session/user-role message IDs;
case-sensitive feedback ratings; HTML/script-like message content
(confirmed stored as inert plain text server-side); admin credential
verification edge cases (wrong password, wrong username, unset hash,
empty password).

**What this does and does not prove:** each test confirms the *specific
behavior it asserts* — e.g. "a SQL-injection-style session ID returns
`False` safely" proves parameterized queries prevent that one class of
injection in that one function. It is not a claim of comprehensive
security auditing, penetration testing, or proof against all possible
attack classes.

## 9. Final test results (this run)

| Suite | Pass | Fail | Notes |
|---|---:|---:|---|
| test_admin | 41 | 0 | |
| test_conversation_memory | 33 | 0 | |
| test_escalation | 30 | 0 | |
| test_evaluation_logic | 3 | 0 | |
| test_evaluation_metrics | 118 | 0 | |
| test_feedback | 25 | 0 | |
| test_frontend | 30 | 0 | |
| **test_integration_flows** | **31** | **0** | Day 14 |
| test_intent_router | 15 | 0 | |
| **test_knowledge_base_edge_cases** | **18** | **0** | Day 14 |
| test_knowledge_base_loader | 4 | 0 | |
| test_llm_provider | 2 | 0 | |
| **test_llm_provider_mocking** | **18** | **0** | Day 14 |
| test_preprocessing | 0 | 0 | SKIPPED — spaCy not installed |
| test_response_generator | 10 | 0 | |
| **test_response_generator_hardening** | **8** | **0** | Day 14 |
| **test_retrieval_edge_cases** | **11** | **0** | Day 14 |
| test_retrieval_logic | 6 | 0 | |
| test_vector_store_logic | 4 | 0 | |
| **TOTAL** | **407** | **0** | across 18 executable suites |

Day 14 specifically added **5 new files / 86 new checks**
(31+18+18+8+11), all genuinely executed and passing.

## 10. Environment-dependent tests NOT executable here

Unchanged from every prior day's honest disclosure — this sandbox has no
internet access, so:
- **spaCy** — `test_preprocessing.py` cannot import the library at all.
- **sentence-transformers / ChromaDB** — real embedding/retrieval
  metrics remain unavailable (correctly reported as such by
  `scripts/run_evaluation.py`, not faked).
- **A real Gemini API key / network** — no real LLM call was made
  anywhere; all provider tests use fakes/mocks as documented above.
- **A browser / live FastAPI server** — no HTTP-layer or rendering test
  was performed; `test_integration_flows.py` explicitly documents this
  and tests the underlying functions in the same sequence and logic the
  real routes use instead.

## 11. Remaining limitations

- `POST /chat` is still Day 1's echo behavior — the intent → retrieval →
  LLM → escalation pipeline is not wired into it yet, so no test here
  (or anywhere in the project so far) covers real end-to-end answer
  quality.
- Test coverage percentage is **not reported** — no coverage tool
  (`coverage.py`, `pytest-cov`) is installed in this sandbox, and no
  percentage is invented.
- The admin `require_admin` FastAPI dependency itself
  (`app/admin_dependencies.py`) cannot be imported or tested at all
  without FastAPI installed — its underlying logic
  (`validate_admin_session`) is fully tested, but the dependency
  wrapper's header-parsing is not.

## 12. Recommendations for Day 15

1. Wire the intent → retrieval → LLM → escalation pipeline into
   `POST /chat` (still pending since Day 1) — this is the single biggest
   gap between "well-tested components" and "a tested chatbot."
2. Run this entire suite, plus `scripts/build_index.py` and
   `scripts/run_evaluation.py`, on a machine with
   sentence-transformers/ChromaDB/a Gemini key installed, and fold the
   real numbers into the evaluation report before using it as the final
   deliverable.
3. Run the frontend in an actual browser at least once before the demo —
   nothing in this project has ever rendered in a real browser.
4. Consider installing `pytest` + `coverage.py` locally for a real
   coverage percentage and standard test discovery/reporting.
