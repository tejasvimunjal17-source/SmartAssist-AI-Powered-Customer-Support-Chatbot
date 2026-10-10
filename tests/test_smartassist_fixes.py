"""
Regression + feature tests for the repeated-escalation fix and the
performance work. Covers: greetings, general support, delayed order,
password reset, general knowledge, explicit human request, mocked Gemini
success, missing credentials, timeouts/API errors, used_llm accuracy,
escalation metadata, history + feedback compatibility, duplicate-request
prevention, timing instrumentation, and frontend loading/error states.

ALL Gemini behaviour here is MOCKED. These tests do NOT prove the real
Gemini API works - use scripts/smoke_test_gemini.py with a real key.

Run with:
    python -m tests.test_smartassist_fixes
"""

import json
import logging
import os
import shutil
import subprocess
import sys
import tempfile
import time
from unittest import mock

from app import inflight
from app.chat_orchestrator import (
    ESCALATION_REPLY, FRUSTRATION_REPLY, GREETING_REPLY, _is_pure_greeting, handle_chat_message,
)
from app.conversation_memory import get_recent_history, init_db
from app.feedback import submit_feedback
from app.llm_provider import (
    LLMAPIError, LLMProvider, LLMRateLimitError, LLMTimeoutError, MissingAPIKeyError, reset_default_provider,
)
from app.response_generator import FALLBACK_LLM_NOT_CONFIGURED
from app.timing import StageTimer

_failures = 0


def check(cond, desc):
    global _failures
    print(f"[{'PASS' if cond else 'FAIL'}] {desc}")
    if not cond:
        _failures += 1


def with_db(fn):
    d = tempfile.mkdtemp()
    try:
        path = os.path.join(d, "t.db")
        init_db(db_path=path)
        return fn(path)
    finally:
        shutil.rmtree(d, ignore_errors=True)


# ---- fakes -----------------------------------------------------------------
class OkLLM(LLMProvider):
    def __init__(self, text="Here is a helpful answer.", delay=0.0):
        self.text, self.delay, self.calls, self.last_user, self.last_system = text, delay, 0, None, None

    def generate(self, system_prompt, user_prompt):
        self.calls += 1
        self.last_system, self.last_user = system_prompt, user_prompt
        if self.delay:
            time.sleep(self.delay)
        return self.text


class RaisingLLM(LLMProvider):
    def __init__(self, exc):
        self.exc = exc

    def generate(self, *_):
        raise self.exc


def kb(title, body, distance=0.4, aid="kb-1"):
    class C:
        def query(self, **kw):
            return {"ids": [[aid]], "documents": [[body]],
                    "metadatas": [[{"title": title, "category": "x", "source_filename": "f.md", "source_path": "/f.md"}]],
                    "distances": [[distance]]}
    return C()


class NoKB:
    def query(self, **kw):
        return {"ids": [[]], "documents": [[]], "metadatas": [[]], "distances": [[]]}


PASSWORD_KB = lambda: kb("How do I reset my password?", "Click Forgot Password on the login page.", aid="account-002")
SHIPPING_KB = lambda: kb("How long does shipping take?", "Orders arrive within 3-5 business days.", aid="shipping-002")


def weak_embed(texts):
    """Low, unrelated similarity for everything - the situation that used to cause escalation."""
    import hashlib
    return [[b / 255 - 0.5 for b in hashlib.sha256(t.lower().encode()).digest()[:16]] for t in texts]


def no_embedder(texts):
    raise RuntimeError("embedding model unavailable")


def chat(db, msg, llm=None, col=None, embed=weak_embed, sid=None):
    return handle_chat_message(msg, sid, db_path=db, provider=llm or OkLLM(), embed_fn=embed,
                               collection=col if col is not None else NoKB())


# ---- 1. greetings -------------------------------------------------------------
def test_greetings():
    class MustNotRun:
        def query(self, **kw):
            raise AssertionError("retrieval must not run for a pure greeting")

    def _t(db):
        for g in ["hi", "Hello!", "hey there", "good morning, how are you?"]:
            llm = OkLLM()
            r = chat(db, g, llm, MustNotRun())
            check(r.reply == GREETING_REPLY and not r.used_llm and not r.escalated and llm.calls == 0,
                  f"greeting {g!r} -> scripted reply, no retrieval, no LLM, used_llm=False")
    with_db(_t)
    check(not _is_pure_greeting("Hello, how do I reset my password?"), "'Hello, how do I reset my password?' is NOT a pure greeting")
    check(not _is_pure_greeting("hi my order is late"), "'hi my order is late' is NOT a pure greeting")

    def _t2(db):
        llm = OkLLM("Use Forgot Password on the login page.")
        r = chat(db, "Hello, how do I reset my password?", llm, PASSWORD_KB())
        check(llm.calls == 1 and r.used_llm and r.reply != GREETING_REPLY,
              "a greeting + real question goes to the LLM instead of being swallowed by the greeting reply (old bug)")
    with_db(_t2)


# ---- 2-5. ordinary questions are answered, never escalated -----------------------
def test_ordinary_questions_are_answered_not_escalated():
    cases = [
        ("general support", "can you help me with my account", NoKB()),
        ("delayed order", "my order is late", SHIPPING_KB()),
        ("delayed order (phrasing 2)", "where is my package, it hasn't arrived", SHIPPING_KB()),
        ("password reset", "I forgot my password", PASSWORD_KB()),
        ("password reset (phrasing 2)", "i can't remember my login password", PASSWORD_KB()),
        ("general knowledge", "What is the capital of France?", NoKB()),
    ]
    for embed_name, embed in [("weak-similarity embedder", weak_embed), ("embedder unavailable", no_embedder)]:
        for label, msg, col in cases:
            def _t(db, msg=msg, col=col):
                llm = OkLLM("A useful reply.")
                r = chat(db, msg, llm, col, embed=embed)
                return r, llm
            r, llm = with_db(_t)
            check(r.escalated is False and r.escalation_reason is None and r.used_llm is True and r.reply == "A useful reply.",
                  f"[{embed_name}] {label}: answered by the LLM, not escalated")

    def _t(db):
        llm = OkLLM("Paris.")
        r = chat(db, "What is the capital of France?", llm, NoKB())
        check("No matching help articles" in llm.last_user and r.articles_used == [],
              "general knowledge: LLM is told no article matched; no articles are claimed as used")
    with_db(_t)

    def _t2(db):
        llm = OkLLM("Orders usually arrive in 3-5 business days.")
        r = chat(db, "my order is late", llm, SHIPPING_KB())
        check(r.articles_used == ["shipping-002"] and "3-5 business days" in llm.last_user,
              "delayed order: the shipping article reaches the prompt and is reported in articles_used")
    with_db(_t2)


# ---- 6 & 11. explicit human request + metadata ---------------------------------------
def test_explicit_human_request_and_metadata():
    def _t(db):
        llm = OkLLM()
        r = chat(db, "I want to talk to a human agent please", llm, PASSWORD_KB())
        check(r.escalated is True and r.escalation_reason == "explicit_human_request", "explicit request escalates with reason explicit_human_request")
        check(llm.calls == 0 and r.used_llm is False and r.articles_used == [], "no LLM/retrieval on explicit escalation; used_llm=False")
        check(r.reply == ESCALATION_REPLY, "the honest handoff reply is returned")
        check(r.handoff_confirmed is False, "handoff_confirmed is False (no live-agent integration exists)")
        low = r.reply.lower()
        check("connect you with" not in low and "has been contacted" not in low and "i've contacted" not in low and "notified" in low and "haven't notified" in low,
              "reply never claims a human was contacted")
        check(isinstance(r.intent, str) and isinstance(r.intent_confidence, float), "intent + intent_confidence metadata present")
    with_db(_t)

    def _t2(db):
        r = chat(db, "This is ridiculous, it's still not working!! I need this fixed", OkLLM(), PASSWORD_KB())
        check(r.escalated and r.escalation_reason in ("frustration_detected", "multiple_escalation_signals") and r.reply == FRUSTRATION_REPLY and r.handoff_confirmed is False,
              "genuine frustration still escalates, with an honest reply and handoff_confirmed=False")
    with_db(_t2)

    def _t3(db):
        r = chat(db, "blah blah unclear", OkLLM(), NoKB())
        check(r.escalated is False and r.escalation_reason is None, "non-escalated responses carry escalated=False, reason=None")
    with_db(_t3)


# ---- 7. mocked Gemini success ------------------------------------------------------------------
def test_mocked_gemini_success():
    def _t(db):
        llm = OkLLM("Go to the login page and click Forgot Password.")
        r = chat(db, "how do I reset my password", llm, PASSWORD_KB())
        check(r.reply == "Go to the login page and click Forgot Password." and r.used_llm is True and r.fallback_reason is None,
              "the (mocked) Gemini reply reaches the caller unchanged; used_llm=True; no fallback reason")
        check(llm.calls == 1, "exactly ONE LLM call is made per message")
    with_db(_t)


# ---- 8. missing credentials --------------------------------------------------------------------
def test_missing_credentials():
    reset_default_provider()
    env = {k: v for k, v in os.environ.items() if k != "GEMINI_API_KEY"}

    def _t(db):
        with mock.patch.dict(os.environ, env, clear=True):
            reset_default_provider()
            # provider=None -> the REAL default-provider path, with no key set
            r = handle_chat_message("I forgot my password", db_path=db, embed_fn=weak_embed, collection=PASSWORD_KB())
        check(r.escalated is False, "missing API key does NOT escalate")
        check(r.used_llm is False and r.fallback_reason == "missing_api_key", "used_llm=False and fallback_reason='missing_api_key'")
        check("Forgot Password" in r.reply and "isn't set up" in r.reply, "reply shows the relevant help-article text plus an honest note")
        check(r.articles_used == ["account-002"], "the article shown is reported in articles_used")
    with_db(_t)

    def _t2(db):
        with mock.patch.dict(os.environ, env, clear=True):
            reset_default_provider()
            r = handle_chat_message("What is the capital of France?", db_path=db, embed_fn=weak_embed, collection=NoKB())
        check(r.reply == FALLBACK_LLM_NOT_CONFIGURED and r.used_llm is False and not r.escalated,
              "no key + no article -> helpful 'not configured' message, not an escalation")
    with_db(_t2)
    reset_default_provider()


# ---- 9 & 10. timeouts / API errors / used_llm accuracy ---------------------------------------------
def test_llm_failures_and_used_llm_accuracy():
    cases = [
        (LLMTimeoutError("t"), "llm_timeout"), (LLMRateLimitError("r"), "llm_rate_limited"),
        (LLMAPIError("a"), "llm_error"), (MissingAPIKeyError("k"), "missing_api_key"), (ValueError("weird sdk bug"), "llm_error"),
    ]
    for exc, reason in cases:
        def _t(db, exc=exc):
            return chat(db, "how do I reset my password", RaisingLLM(exc), PASSWORD_KB())
        r = with_db(_t)
        check(r.used_llm is False and r.fallback_reason == reason and r.escalated is False and bool(r.reply.strip()),
              f"{type(exc).__name__}: used_llm=False, reason={reason!r}, not escalated, non-empty reply")
        check("Forgot Password" in r.reply, f"{type(exc).__name__}: the relevant article text is still shown")
        check("contacted" not in r.reply.lower() or "haven't" in r.reply.lower(), f"{type(exc).__name__}: no false claim of human contact")

    def _t(db):
        r = chat(db, "how do I reset my password", RaisingLLM(LLMTimeoutError("t")), NoKB())
        check(r.used_llm is False and r.fallback_reason == "llm_timeout" and "try again" in r.reply.lower(), "timeout with no article -> honest 'try again' message")
    with_db(_t)

    def _t2(db):
        ok = chat(db, "how do I reset my password", OkLLM(), PASSWORD_KB())
        greet = chat(db, "hi", OkLLM(), PASSWORD_KB())
        esc = chat(db, "talk to a human", OkLLM(), PASSWORD_KB())
        bad = chat(db, "how do I reset my password", RaisingLLM(LLMAPIError("x")), PASSWORD_KB())
        check((ok.used_llm, greet.used_llm, esc.used_llm, bad.used_llm) == (True, False, False, False),
              "used_llm is True ONLY when the LLM really produced the reply (ok/greeting/escalation/failure = T/F/F/F)")
    with_db(_t2)


# ---- 12. history + feedback compatibility -----------------------------------------------------------
def test_history_and_feedback_compat():
    def _t(db):
        r1 = chat(db, "how do I reset my password", OkLLM("Step 1..."), PASSWORD_KB())
        r2 = chat(db, "thanks, and how long does the link last?", OkLLM("30 minutes."), PASSWORD_KB(), sid=r1.session_id)
        check(r2.session_id == r1.session_id, "the session id is continued")
        hist = get_recent_history(r1.session_id, db_path=db)
        check([h["role"] for h in hist] == ["user", "assistant", "user", "assistant"], "history stores both sides, in order")
        check(all({"id", "role", "content", "timestamp"} <= set(h) for h in hist), "history items keep the id/role/content/timestamp contract")
        res = submit_feedback(r1.session_id, r1.message_id, "helpful", None, db_path=db)
        check(isinstance(res, dict) and res.get("id"), "feedback can be attached to the assistant message_id returned by /chat")
        llm = OkLLM()
        chat(db, "and what about the second one", llm, PASSWORD_KB(), sid=r1.session_id)
        check("CONVERSATION HISTORY" in llm.last_user and "how do I reset my password" in llm.last_user, "prior turns reach the prompt")
        fields = set(r1.__dict__)
        check({"reply", "session_id", "message_id", "intent", "intent_confidence", "escalated", "escalation_reason", "articles_used", "used_llm"} <= fields,
              "every pre-existing /chat response field is still present")
    with_db(_t)


# ---- 13. duplicate request prevention -----------------------------------------------------------------
def test_duplicate_request_prevention():
    check(inflight.try_acquire("sess-A") is True, "first request for a session acquires the guard")
    check(inflight.try_acquire("sess-A") is False, "a concurrent second request for the same session is rejected")
    check(inflight.try_acquire("sess-B") is True, "a different session is unaffected")
    inflight.release("sess-A")
    check(inflight.try_acquire("sess-A") is True, "the guard is released after the first request finishes")
    inflight.release("sess-A"); inflight.release("sess-B")

    src = open(os.path.join(os.path.dirname(__file__), "..", "app", "main.py"), encoding="utf-8").read()
    check("status_code=429" in src and "acquire_inflight(session_id)" in src and "release_inflight(session_id)" in src and "finally:" in src,
          "main.py's /chat returns 429 for a concurrent duplicate and always releases the guard (try/finally)")

    # the guard is released even when the pipeline raises
    import ast
    tree = ast.parse(src)
    chat_fn = next(n for n in ast.walk(tree) if isinstance(n, ast.FunctionDef) and n.name == "chat")
    check(any(isinstance(n, ast.Try) and n.finalbody for n in ast.walk(chat_fn)), "release happens in a finally block (AST-verified)")


# ---- 14. performance instrumentation --------------------------------------------------------------------
def test_performance_instrumentation():
    def _t(db):
        r = chat(db, "how do I reset my password", OkLLM(delay=0.15), PASSWORD_KB())
        t = r.timings_ms
        check({"preprocess", "history", "intent", "escalation", "retrieval", "llm", "persist", "total", "local", "llm_wait"} <= set(t),
              f"all stages are timed: {sorted(t)}")
        check(t["llm_wait"] >= 140, f"llm_wait reflects the (fake) 150ms Gemini delay: {t['llm_wait']}ms")
        check(t["local"] < t["total"] and abs((t["local"] + t["llm_wait"]) - t["total"]) < 2.0, "local + llm_wait == total (local processing separated from Gemini wait)")
        check(t["local"] < 100, f"local processing with fakes is small: {t['local']}ms")
    with_db(_t)

    # structured log: JSON, stage names + numbers only, never content or secrets
    records = []
    h = logging.Handler()
    h.emit = lambda rec: records.append(rec.getMessage())
    lg = logging.getLogger("smartassist.perf")
    lg.addHandler(h); lg.setLevel(logging.INFO)
    secret_text = "MY-PRIVATE-ORDER-4471 password hunter2"
    try:
        with mock.patch.dict(os.environ, {"GEMINI_API_KEY": "AIza-SECRET-KEY-123"}):
            with_db(lambda db: chat(db, secret_text, OkLLM("fine"), PASSWORD_KB()))
    finally:
        lg.removeHandler(h)
    check(len(records) == 1, "exactly one structured timing log line per request")
    line = records[0] if records else ""
    parsed = json.loads(line) if line else {}
    check(parsed.get("event") == "chat_timing" and "total" in parsed.get("ms", {}), "the log line is valid JSON with an ms.total value")
    check("4471" not in line and "hunter2" not in line and "SECRET-KEY" not in line, "the log line contains no message content and no API key")

    src = open(os.path.join(os.path.dirname(__file__), "..", "app", "timing.py"), encoding="utf-8").read()
    check("perf_counter" in src and "time.time()" not in src, "timing uses the monotonic perf_counter clock, not wall-clock time")

    tm = StageTimer()
    with tm.stage("x"):
        time.sleep(0.02)
    check(15 <= tm.stages["x"] <= 200, "StageTimer measures a stage in milliseconds")


# ---- 15. frontend loading / error states (real app.js run under node) -----------------------------------
def test_frontend_behavior():
    node = shutil.which("node")
    if not node:
        print("[SKIPPED] node not installed - frontend behaviour harness not run")
        return
    script = os.path.join(os.path.dirname(__file__), "frontend_behavior.js")
    proc = subprocess.run([node, script], capture_output=True, text=True, timeout=60)
    out = proc.stdout.strip().splitlines()
    for line in out:
        print("   " + line)
    check(proc.returncode == 0 and "ALL FRONTEND BEHAVIOR TESTS PASSED" in proc.stdout,
          "frontend loading/duplicate-submit/timeout/error behaviour verified by executing the real app.js")


# ---- misc: escalation fix guard + config sanity ------------------------------------------------------
def test_root_cause_guard_and_config():
    from app import config
    check(config.ESCALATE_ON_LOW_CONFIDENCE is False, "low classifier confidence alone does not escalate by default")
    check(config.GEMINI_MODEL_NAME != "gemini-1.5-flash", "the retired gemini-1.5-flash model is not the default")
    check(config.LLM_MAX_RETRIES <= 2 and config.LLM_REQUEST_TIMEOUT_SECONDS > 0, "retries bounded; timeout configured")
    check(config.RUN_SPACY_PREPROCESSING is False, "spaCy is off the per-request path by default")
    src = open(os.path.join(os.path.dirname(__file__), "..", "app", "llm_provider.py"), encoding="utf-8").read()
    check("os.environ.get(GEMINI_API_KEY_ENV_VAR)" in src and "AIza" not in src, "API key is read from the environment only")

    # embedding is computed once per request and shared by intent + retrieval
    calls = []

    def counting_embed(texts):
        calls.append(list(texts))
        return weak_embed(texts)

    with_db(lambda db: chat(db, "I forgot my password", OkLLM(), PASSWORD_KB(), embed=counting_embed))
    single = [c for c in calls if len(c) == 1]
    check(len(single) == 1, f"the user's message is embedded ONCE per request (shared by intent + retrieval), saw {len(single)}")


def run():
    for name, fn in list(globals().items()):
        if name.startswith("test_") and callable(fn):
            print(f"\n--- {name} ---")
            fn()
    print("-" * 70)
    if _failures == 0:
        print("ALL SMARTASSIST FIX TESTS PASSED (Gemini is MOCKED - real API not exercised)")
    else:
        print(f"{_failures} TEST(S) FAILED")
        sys.exit(1)


if __name__ == "__main__":
    run()
