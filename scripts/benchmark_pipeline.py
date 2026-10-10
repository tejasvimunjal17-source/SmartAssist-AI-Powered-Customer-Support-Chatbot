"""
Measures the LOCAL cost of one /chat request (everything except Gemini and
real model inference) with identical fakes, so two versions of the code can
be compared fairly.

What it measures (all real, for the code in the current directory):
  * wall time of handle_chat_message() per request (monotonic clock), p50/p95
  * embedding calls and texts embedded per request
  * how many times a vector-store client is opened per request
  * LLM calls per request

What it does NOT measure: real sentence-transformers inference time, real
ChromaDB query time, or real Gemini latency. Use the structured
"chat_timing" log lines on the deployed app for those.

Usage:
    python -m scripts.benchmark_pipeline            # 200 requests per scenario
    python -m scripts.benchmark_pipeline --n 500
"""

import argparse
import os
import shutil
import statistics
import sys
import tempfile
import time
import types

# Fake chromadb so get_collection() can be exercised without the package;
# counts how many clients get opened.
STATE = {"client_opens": 0, "embed_calls": 0, "embed_texts": 0, "llm_calls": 0}


class _FakeCol:
    def count(self):
        return 1

    def query(self, **kw):
        return {"ids": [["account-002"]], "documents": [["Click Forgot Password on the login page."]],
                "metadatas": [[{"title": "Reset password", "category": "account", "source_filename": "f.md", "source_path": "/f.md"}]],
                "distances": [[0.5]]}


class _FakeClient:
    def __init__(self, path=None):
        STATE["client_opens"] += 1

    def get_or_create_collection(self, name=None):
        return _FakeCol()


_fake_chroma = types.ModuleType("chromadb")
_fake_chroma.PersistentClient = _FakeClient
sys.modules["chromadb"] = _fake_chroma


def counting_embed(texts):
    STATE["embed_calls"] += 1
    STATE["embed_texts"] += len(texts)
    return [[(hash((t, i)) % 1000) / 1000 for i in range(8)] for t in texts]


def main():
    ap = argparse.ArgumentParser()
    ap.add_argument("--n", type=int, default=200)
    args = ap.parse_args()

    sys.path.insert(0, os.getcwd())
    # Replace the DEFAULT embedder (before any app module imports it) so the
    # code runs its normal default-embedder path - including the example-vector
    # cache the real embedder benefits from - while we count calls.
    import app.embeddings as embeddings_module
    embeddings_module.embed_texts = counting_embed
    from app.chat_orchestrator import handle_chat_message
    from app.conversation_memory import init_db
    from app.llm_provider import LLMProvider

    class FakeLLM(LLMProvider):
        def generate(self, s, u):
            STATE["llm_calls"] += 1
            return "A helpful answer."

    scenarios = [
        ("greeting", "hello"),
        ("password reset (no rule phrase)", "I forgot my password"),
        ("delayed order (no rule phrase)", "my order is late"),
        ("rule-matched FAQ", "How do I reset my password?"),
    ]
    d = tempfile.mkdtemp()
    db = os.path.join(d, "bench.db")
    init_db(db)
    try:
        # one untimed request first: pays one-time costs (imports, caches)
        handle_chat_message("warm up request", db_path=db, provider=FakeLLM())
        print(f"{'scenario':36} {'p50 ms':>8} {'p95 ms':>8} {'embed calls':>12} {'texts':>6} {'chroma opens':>13} {'llm calls':>10}")
        for name, msg in scenarios:
            for k in STATE:
                STATE[k] = 0
            times = []
            for _ in range(args.n):
                t0 = time.perf_counter()
                handle_chat_message(msg, db_path=db, provider=FakeLLM())
                times.append((time.perf_counter() - t0) * 1000)
            times.sort()
            p50 = statistics.median(times)
            p95 = times[int(len(times) * 0.95) - 1]
            n = args.n
            print(f"{name:36} {p50:8.2f} {p95:8.2f} {STATE['embed_calls']/n:12.2f} {STATE['embed_texts']/n:6.1f} "
                  f"{STATE['client_opens']/n:13.2f} {STATE['llm_calls']/n:10.2f}")
    finally:
        shutil.rmtree(d, ignore_errors=True)


if __name__ == "__main__":
    main()
