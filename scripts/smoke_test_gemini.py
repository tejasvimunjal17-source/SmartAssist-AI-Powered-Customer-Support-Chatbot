"""
OPTIONAL real-Gemini smoke test. Makes REAL network calls and uses your
real API quota (a few tiny requests). The automated test suite never does
this - it only uses mocks, which cannot prove the real API works.

Usage (key stays in the environment, never printed):
    export GEMINI_API_KEY=...        # Windows PowerShell: $env:GEMINI_API_KEY="..."
    python -m scripts.smoke_test_gemini
    GEMINI_MODEL=gemini-2.5-flash-lite python -m scripts.smoke_test_gemini   # compare models

Exit code 0 = every step passed, 1 = something failed.
"""

import os
import sys
import time

sys.path.insert(0, os.getcwd())


def main() -> int:
    from app.config import GEMINI_API_KEY_ENV_VAR, GEMINI_MODEL_NAME

    if not os.environ.get(GEMINI_API_KEY_ENV_VAR):
        print(f"SKIPPED: {GEMINI_API_KEY_ENV_VAR} is not set.")
        return 1

    from app.llm_provider import GeminiProvider, LLMError

    print(f"Model: {GEMINI_MODEL_NAME}")
    provider = GeminiProvider()
    ok = True

    # 1. direct provider call (twice: first includes client creation + TLS setup)
    for label in ("first call (cold client)", "second call (warm client)"):
        t0 = time.perf_counter()
        try:
            text = provider.generate("You are a terse assistant.", "Reply with exactly: pong")
            ms = (time.perf_counter() - t0) * 1000
            print(f"[PASS] {label}: {ms:.0f} ms -> {text[:60]!r}")
        except LLMError as exc:
            print(f"[FAIL] {label}: {type(exc).__name__}: {exc}")
            ok = False
            break

    # 2. full pipeline with the real provider and a temp database
    import shutil
    import tempfile

    from app.chat_orchestrator import handle_chat_message
    from app.conversation_memory import init_db

    d = tempfile.mkdtemp()
    try:
        db = os.path.join(d, "smoke.db")
        init_db(db)
        for q in ["I forgot my password", "My order is late", "What is the capital of France?"]:
            t0 = time.perf_counter()
            r = handle_chat_message(q, db_path=db, provider=provider)
            ms = (time.perf_counter() - t0) * 1000
            good = r.used_llm and not r.escalated
            ok = ok and good
            print(f"[{'PASS' if good else 'FAIL'}] {q!r}: {ms:.0f} ms, used_llm={r.used_llm}, escalated={r.escalated}, "
                  f"fallback_reason={r.fallback_reason}, articles={r.articles_used}")
            print(f"        timings_ms={r.timings_ms}")
            print(f"        reply: {r.reply[:140]!r}")
    finally:
        shutil.rmtree(d, ignore_errors=True)

    print("SMOKE TEST PASSED" if ok else "SMOKE TEST FAILED")
    return 0 if ok else 1


if __name__ == "__main__":
    sys.exit(main())
