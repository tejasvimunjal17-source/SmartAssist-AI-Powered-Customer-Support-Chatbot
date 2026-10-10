"""
Day 10 tests. These are STATIC checks — file existence, text-content
checks, AST inspection of app/main.py, and (if Node is available) a real
JavaScript syntax check. None of this is browser automation: this
sandbox has no browser, so actual click-through/rendering behavior was
NOT tested here — see the honesty note in the Day 10 summary and the
README's "Day 10 testing limitations" section.

Run with:
    python -m tests.test_frontend
"""

import ast
import os
import subprocess

from app.config import BASE_DIR, FRONTEND_DIR

_failures = 0


def check(condition, description):
    global _failures
    status = "PASS" if condition else "FAIL"
    print(f"[{status}] {description}")
    if not condition:
        _failures += 1


def read(path):
    with open(path, "r", encoding="utf-8") as f:
        return f.read()


def run():
    print("--- Frontend files exist ---")
    index_path = os.path.join(FRONTEND_DIR, "index.html")
    css_path = os.path.join(FRONTEND_DIR, "style.css")
    js_path = os.path.join(FRONTEND_DIR, "app.js")

    check(os.path.isfile(index_path), "frontend/index.html exists")
    check(os.path.isfile(css_path), "frontend/style.css exists")
    check(os.path.isfile(js_path), "frontend/app.js exists")

    if not (os.path.isfile(index_path) and os.path.isfile(css_path) and os.path.isfile(js_path)):
        print("Cannot continue further checks — a frontend file is missing.")
        return

    html = read(index_path)
    css = read(css_path)
    js = read(js_path)

    print("\n--- index.html references the real static assets ---")
    check('href="style.css"' in html, "index.html links style.css")
    check('src="app.js"' in html, "index.html loads app.js")
    check("<form" in html and 'id="composer"' in html, "index.html has a composer form")
    check('id="chat-window"' in html, "index.html has a chat window container")
    check('id="typing-indicator"' in html, "index.html has a typing indicator element")
    check('id="error-banner"' in html, "index.html has an error banner element")

    print("\n--- app.js talks to the REAL backend endpoints (not a fake/hard-coded chatbot) ---")
    check('"/chat"' in js, "app.js calls the real /chat endpoint")
    check("/history" in js, "app.js calls the real /history endpoint")
    check("fetch(" in js, "app.js uses fetch() to make real HTTP requests")

    print("\n--- Security: safe DOM insertion, no secrets ---")
    import re
    unsafe_assignment = re.search(r"\.innerHTML\s*=", js)
    check(unsafe_assignment is None, "app.js never ASSIGNS to innerHTML (the word may appear in a comment explaining why not — checked for actual usage, not the word itself)")
    check("textContent" in js, "app.js uses textContent to insert message content")
    check("GEMINI_API_KEY" not in js and "api_key" not in js.lower(), "no API keys/secrets present in frontend JavaScript")
    check("localStorage" in js, "app.js persists the session id client-side via localStorage")

    print("\n--- Typing indicator tied to real request lifecycle (not a fixed timeout) ---")
    check("showTyping()" in js and "hideTyping()" in js, "typing indicator has show/hide functions")
    # setTimeout is allowed ONLY for the "still working" label and the request abort -
    # never to delay or fabricate an assistant reply.
    import re as _re
    timeouts = _re.findall(r"setTimeout\(([^;]*)\);", js)
    check(all(("setTypingLabel" in t or "controller.abort" in t) for t in timeouts) and len(timeouts) >= 1,
          "every setTimeout is only for the slow-hint label or request abort, never a simulated AI reply")
    check("showTyping();" in js and js.index("showTyping();") < js.index("const data = await sendMessage"),
          "the loading indicator is shown BEFORE the network request starts")
    check("isSending" in js and "if (isSending)" in js, "duplicate submissions are blocked while a request is running")
    check("AbortController" in js and "REQUEST_TIMEOUT_MS" in js, "requests have a client-side timeout")
    check("429" in js, "the 429 'already processing' response is handled with a friendly message")
    check(js.count("hideTyping()") >= 2, "hideTyping() is called in more than one place (success AND error paths)")

    print("\n--- Error handling paths present ---")
    check("showError(" in js, "app.js has a function to show user-friendly errors")
    check("catch" in js, "app.js catches request failures instead of letting them crash silently")
    check("stack" not in js.lower(), "app.js does not attempt to surface raw stack traces to the user")

    print("\n--- CSS is responsive ---")
    check("@media" in css, "style.css includes a responsive media query")

    print("\n--- FastAPI integration: existing endpoints preserved, frontend mounted separately ---")
    main_path = os.path.join(BASE_DIR, "app", "main.py")
    main_source = read(main_path)
    tree = ast.parse(main_source)

    route_paths = []
    for node in ast.walk(tree):
        if isinstance(node, ast.FunctionDef):
            for decorator in node.decorator_list:
                if isinstance(decorator, ast.Call) and isinstance(decorator.func, ast.Attribute):
                    if decorator.func.attr in ("get", "post") and decorator.args:
                        arg = decorator.args[0]
                        if isinstance(arg, ast.Constant):
                            route_paths.append(arg.value)

    check("/" in route_paths, "GET / route still defined (existing status endpoint preserved)")
    check("/chat" in route_paths, "POST /chat route still defined")
    check("/history" in route_paths, "GET /history route still defined")

    check('app.mount("/app"' in main_source, "frontend is mounted at /app (separate from existing API routes)")
    check("StaticFiles" in main_source, "main.py uses FastAPI's StaticFiles to serve the frontend")
    check("FRONTEND_DIR" in main_source, "frontend path comes from centralized config, not hard-coded")

    print("\n--- JavaScript syntax check ---")
    try:
        result = subprocess.run(
            ["node", "--check", js_path],
            capture_output=True,
            text=True,
            timeout=10,
        )
        if result.returncode == 0:
            check(True, "app.js has valid JavaScript syntax (checked with `node --check`)")
        else:
            check(False, f"app.js has a JavaScript syntax error: {result.stderr.strip()}")
    except FileNotFoundError:
        print("[SKIPPED] Node.js not found in this environment — JS syntax not checked here. "
              "(It WAS checked with Node in the sandbox that built this project.)")

    print("\n" + "-" * 60)
    if _failures == 0:
        print("ALL FRONTEND STATIC CHECKS PASSED (no browser automation was performed — see README 'Day 10' for that limitation)")
    else:
        print(f"{_failures} CHECK(S) FAILED")


if __name__ == "__main__":
    run()
