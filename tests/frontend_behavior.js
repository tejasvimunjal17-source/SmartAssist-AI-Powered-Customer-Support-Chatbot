// Executes the REAL frontend/app.js against a minimal fake DOM + fake fetch,
// to test loading/error/duplicate-submit behaviour. No browser is involved,
// so layout/rendering on a real Android phone is NOT covered by this.
// Run: node tests/frontend_behavior.js   (prints [PASS]/[FAIL] lines)
const fs = require("fs");
const path = require("path");
const vm = require("vm");

let failures = 0;
function check(cond, desc) {
  console.log(`[${cond ? "PASS" : "FAIL"}] ${desc}`);
  if (!cond) failures++;
}
let completed = false;
process.on("exit", () => {
  if (!completed) { console.log("[FAIL] harness did not run to completion (a request/promise hung)"); process.exitCode = 1; }
});
const flush = () => new Promise((r) => setImmediate(r));

function makeEl(id) {
  return {
    id, hidden: false, textContent: "", value: "", disabled: false, readOnly: false,
    className: "", children: [], listeners: {}, attrs: {}, scrollTop: 0, scrollHeight: 100,
    appendChild(c) { this.children.push(c); return c; },
    addEventListener(t, f) { this.listeners[t] = f; },
    setAttribute(k, v) { this.attrs[k] = v; },
    focus() {},
    querySelector(sel) { return sel === ".typing-label" ? this._label : null; },
  };
}

function setup(fetchImpl) {
  const els = {};
  for (const id of ["chat-window", "empty-state", "typing-indicator", "error-banner", "composer", "message-input", "send-button"]) {
    els[id] = makeEl(id);
  }
  els["typing-indicator"].hidden = true;
  els["error-banner"].hidden = true;
  els["typing-indicator"]._label = makeEl("label");
  els["typing-indicator"]._label.textContent = "SmartAssist is typing...";

  const timers = [];
  const store = {};
  const log = { fetchCalls: 0, typingVisibleAtFetch: null };

  const ctx = {
    document: { getElementById: (id) => els[id], createElement: () => makeEl("x") },
    localStorage: { getItem: (k) => (k in store ? store[k] : null), setItem: (k, v) => { store[k] = v; } },
    performance: { now: () => Date.now() },
    console: { debug() {}, log: console.log },
    AbortController, JSON, Promise, Error, encodeURIComponent,
    setTimeout: (fn, ms) => { timers.push({ fn, ms, cancelled: false }); return timers.length; },
    clearTimeout: (id) => { if (timers[id - 1]) timers[id - 1].cancelled = true; },
    fetch: (url, opts) => {
      if (url === "/chat") {
        log.fetchCalls++;
        log.typingVisibleAtFetch = els["typing-indicator"].hidden === false;
      }
      return fetchImpl(url, opts);
    },
  };
  vm.createContext(ctx);
  const code = fs.readFileSync(path.join(__dirname, "..", "frontend", "app.js"), "utf8");
  vm.runInContext(code, ctx);
  return { els, timers, store, log };
}

async function submit(env, text) {
  env.els["message-input"].value = text;
  const p = env.els["composer"].listeners.submit({ preventDefault() {} });
  return p;
}

const okResponse = (body) => Promise.resolve({
  ok: true, status: 200, headers: { get: () => "12.3" }, json: () => Promise.resolve(body),
});

(async () => {
  // 1. Loading indicator appears BEFORE the network call, and success clears it.
  {
    let resolveFetch;
    const env = setup(() => new Promise((r) => { resolveFetch = r; }));
    const pending = submit(env, "my order is late");
    await flush();
    check(env.log.fetchCalls === 1, "a submit sends exactly one /chat request");
    check(env.log.typingVisibleAtFetch === true, "typing indicator is already visible when the request starts");
    check(env.els["send-button"].disabled === true, "send button is disabled while the request runs");
    check(env.els["message-input"].readOnly === true && env.els["message-input"].disabled === false,
      "input is read-only (not disabled) so the Android keyboard stays open");
    check(env.els["chat-window"].children.length === 1, "the user's bubble is shown immediately; no assistant bubble yet (no simulated reply)");

    // 2. Duplicate submit while pending is ignored.
    env.els["message-input"].value = "second tap";
    env.els["composer"].listeners.submit({ preventDefault() {} });  // not awaited on purpose
    await flush();
    check(env.log.fetchCalls === 1, "a second submit while a request is running does NOT send another request");

    resolveFetch(await okResponse({ reply: "Ships in 3-5 days.", session_id: "s1", message_id: 7 }));
    await pending;
    check(env.els["typing-indicator"].hidden === true, "typing indicator hides when the reply arrives");
    check(env.els["send-button"].disabled === false && env.els["message-input"].readOnly === false, "controls are re-enabled after the reply");
    const last = env.els["chat-window"].children[env.els["chat-window"].children.length - 1];
    check(last.children[0].textContent === "Ships in 3-5 days.", "the real backend reply is displayed");
    check(env.store["smartassist_session_id"] === "s1", "session id is stored");
  }

  // 3. Slow-hint label changes only via timer, and is cleared on completion.
  {
    let resolveFetch;
    const env = setup(() => new Promise((r) => { resolveFetch = r; }));
    const pending = submit(env, "hello?");
    await flush();
    const hint = env.timers.find((t) => t.ms === 8000);
    check(!!hint, "a 'still working' hint timer is scheduled (8s)");
    hint.fn();
    check(env.els["typing-indicator"]._label.textContent.startsWith("Still working"), "indicator text changes to 'Still working' for slow replies");
    resolveFetch(await okResponse({ reply: "x", session_id: "s", message_id: 1 }));
    await pending;
    check(hint.cancelled === true, "the hint timer is cancelled when the reply arrives");
  }

  // 4. Network failure -> friendly error, UI usable again.
  {
    const env = setup(() => Promise.reject(new TypeError("Failed to fetch")));
    await submit(env, "hi");
    check(env.els["error-banner"].hidden === false && /Can't reach SmartAssist/.test(env.els["error-banner"].textContent),
      "network error shows a friendly 'can't reach' message");
    check(env.els["typing-indicator"].hidden === true && env.els["send-button"].disabled === false, "UI is usable again after a network error");
  }

  // 5. Client timeout (abort) -> friendly timeout message.
  {
    const env = setup((url, opts) => new Promise((_, reject) => {
      opts.signal.addEventListener("abort", () => { const e = new Error("aborted"); e.name = "AbortError"; reject(e); });
    }));
    const pending = submit(env, "slow one");
    await flush();
    const abortTimer = env.timers.find((t) => t.ms === 35000);
    check(!!abortTimer, "a 35s request timeout is scheduled");
    abortTimer.fn();
    await pending;
    check(/taking longer than expected/.test(env.els["error-banner"].textContent), "a timed-out request shows a friendly timeout message");
    check(env.els["send-button"].disabled === false, "controls re-enabled after a timeout");
  }

  // 6. HTTP 500 and 429.
  {
    const env = setup(() => Promise.resolve({ ok: false, status: 500, headers: { get: () => null }, json: () => Promise.resolve({}) }));
    await submit(env, "hi");
    check(/trouble responding/.test(env.els["error-banner"].textContent), "HTTP 500 shows a friendly error (no internals)");
    const env2 = setup(() => Promise.resolve({ ok: false, status: 429, headers: { get: () => null }, json: () => Promise.resolve({}) }));
    await submit(env2, "hi");
    check(/still being processed/.test(env2.els["error-banner"].textContent), "HTTP 429 shows the 'still processing' message");
  }

  // 7. Empty message sends nothing.
  {
    const env = setup(() => okResponse({ reply: "x", session_id: "s", message_id: 1 }));
    await submit(env, "   ");
    check(env.log.fetchCalls === 0, "an empty/whitespace message sends no request");
  }

  completed = true;
  console.log(failures === 0 ? "ALL FRONTEND BEHAVIOR TESTS PASSED" : `${failures} FRONTEND BEHAVIOR TEST(S) FAILED`);
  process.exit(failures === 0 ? 0 : 1);
})();
