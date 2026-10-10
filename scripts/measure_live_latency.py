"""
Measures END-TO-END latency of a running SmartAssist (local or Railway) from
the client's side, next to the server's own timings.

Usage:
    python -m scripts.measure_live_latency https://YOUR-APP.up.railway.app
    python -m scripts.measure_live_latency https://YOUR-APP.up.railway.app --n 5

Sends a few ordinary questions to POST /chat (each in a NEW session) and
prints: end-to-end ms, server total (X-Process-Time-Ms), local processing,
Gemini wait, used_llm, escalated. Numbers only - no message content stored.
The first request after a deploy/idle period shows cold-start cost.
"""

import argparse
import json
import time
import urllib.request

QUESTIONS = ["I forgot my password", "My order is late", "What is the capital of France?", "hello"]


def post_chat(base, message):
    req = urllib.request.Request(
        base.rstrip("/") + "/chat",
        data=json.dumps({"message": message}).encode(),
        headers={"Content-Type": "application/json"},
        method="POST",
    )
    t0 = time.perf_counter()
    with urllib.request.urlopen(req, timeout=60) as resp:
        body = json.loads(resp.read())
        server_ms = resp.headers.get("X-Process-Time-Ms")
    return (time.perf_counter() - t0) * 1000, server_ms, body


def main():
    ap = argparse.ArgumentParser()
    ap.add_argument("base_url")
    ap.add_argument("--n", type=int, default=2, help="rounds over the question list")
    args = ap.parse_args()

    print(f"{'#':>2} {'e2e ms':>8} {'server ms':>10} {'local ms':>9} {'gemini ms':>10}  used_llm escalated  reason")
    i = 0
    for _ in range(args.n):
        for q in QUESTIONS:
            i += 1
            e2e, server_ms, body = post_chat(args.base_url, q)
            t = body.get("timings_ms") or {}
            print(f"{i:>2} {e2e:8.0f} {str(server_ms):>10} {t.get('local', '-')!s:>9} {t.get('llm_wait', '-')!s:>10}  "
                  f"{body.get('used_llm')!s:8} {body.get('escalated')!s:9} {body.get('fallback_reason') or ''}")


if __name__ == "__main__":
    main()
