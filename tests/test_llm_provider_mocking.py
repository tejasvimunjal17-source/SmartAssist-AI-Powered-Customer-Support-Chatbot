"""
Tests for app/llm_provider.py's GeminiProvider using a FAKE `google.genai`
SDK injected via sys.modules. This exercises GeminiProvider's REAL code
(client construction, config building, error classification, bounded
retry) with no network and no real package.

IMPORTANT: these are MOCKED tests. They prove our code calls the SDK the
way we intend and handles errors correctly. They do NOT prove the real
Gemini API accepts the request - run scripts/smoke_test_gemini.py with a
real key for that.

Run with:
    python -m tests.test_llm_provider_mocking
"""

import sys
import types
from unittest import mock

from app import config
from app import llm_provider
from app.llm_provider import (
    EmptyResponseError,
    GeminiProvider,
    LLMAPIError,
    LLMRateLimitError,
    LLMTimeoutError,
    MissingAPIKeyError,
    get_default_provider,
    reset_default_provider,
)

_failures = 0


def check(condition, description):
    global _failures
    print(f"[{'PASS' if condition else 'FAIL'}] {description}")
    if not condition:
        _failures += 1


class FakeAPIError(Exception):
    """Mimics google.genai.errors.APIError: has an HTTP .code."""
    def __init__(self, code, message="boom"):
        super().__init__(f"{code} {message}")
        self.code = code


class FakeResponse:
    def __init__(self, text):
        self.text = text


def make_fake_sdk(behavior):
    """behavior(call_index, kwargs) -> FakeResponse, or raises."""
    state = {"clients": [], "calls": []}

    class HttpOptions:
        def __init__(self, timeout=None):
            self.timeout = timeout

    class ThinkingConfig:
        def __init__(self, thinking_budget=None):
            self.thinking_budget = thinking_budget

    class GenerateContentConfig:
        def __init__(self, **kw):
            self.__dict__.update(kw)

    class Models:
        def generate_content(self, model, contents, config):
            state["calls"].append({"model": model, "contents": contents, "config": config})
            return behavior(len(state["calls"]) - 1, state["calls"][-1])

    class Client:
        def __init__(self, api_key=None, http_options=None):
            self.api_key = api_key
            self.http_options = http_options
            self.models = Models()
            state["clients"].append(self)

    fake_types = types.ModuleType("google.genai.types")
    fake_types.HttpOptions = HttpOptions
    fake_types.ThinkingConfig = ThinkingConfig
    fake_types.GenerateContentConfig = GenerateContentConfig
    fake_genai = types.ModuleType("google.genai")
    fake_genai.Client = Client
    fake_genai.types = fake_types
    fake_google = types.ModuleType("google")
    fake_google.genai = fake_genai
    return {"google": fake_google, "google.genai": fake_genai, "google.genai.types": fake_types}, state


def with_sdk(behavior, fn):
    modules, state = make_fake_sdk(behavior)
    with mock.patch.dict(sys.modules, modules), mock.patch.object(llm_provider.time, "sleep", lambda s: None):
        fn(state)


def raises(fn):
    try:
        fn()
    except Exception as exc:
        return exc
    return None


def test_success_path_and_request_shape():
    def _t(state):
        p = GeminiProvider(api_key="fake-key")
        reply = p.generate("system rules", "what is my order status?")
        check(reply == "ok reply", "generate() returns the SDK text, stripped")
        check(len(state["clients"]) == 1 and state["clients"][0].api_key == "fake-key", "client built with our API key")
        check(state["clients"][0].http_options.timeout == int(config.LLM_REQUEST_TIMEOUT_SECONDS * 1000),
              "configured request timeout is applied to the client (milliseconds)")
        call = state["calls"][0]
        check(call["model"] == config.GEMINI_MODEL_NAME and call["model"] != "gemini-1.5-flash", "uses the configured, non-retired model")
        check(call["contents"] == "what is my order status?", "user prompt passed as contents")
        check(call["config"].system_instruction == "system rules", "system prompt kept separate in system_instruction")
        check(call["config"].max_output_tokens == config.LLM_MAX_OUTPUT_TOKENS, "output tokens are capped")
        check(call["config"].thinking_config.thinking_budget == config.GEMINI_THINKING_BUDGET, "thinking budget applied for 2.5 Flash")
    with_sdk(lambda i, kw: FakeResponse("  ok reply  "), _t)


def test_client_is_reused_across_calls():
    def _t(state):
        p = GeminiProvider(api_key="k")
        p.generate("s", "one")
        p.generate("s", "two")
        check(len(state["clients"]) == 1, "the SDK client is created once and reused")
        check(len(state["calls"]) == 2, "two calls were made on the shared client")
    with_sdk(lambda i, kw: FakeResponse("x"), _t)


def test_default_provider_is_a_singleton():
    reset_default_provider()
    check(get_default_provider() is get_default_provider(), "get_default_provider() returns one shared provider")
    reset_default_provider()


def test_missing_key():
    def _t(state):
        with mock.patch.dict("os.environ", {}, clear=True):
            exc = raises(lambda: GeminiProvider(api_key=None).generate("s", "u"))
        check(isinstance(exc, MissingAPIKeyError), "no key -> MissingAPIKeyError")
        check(len(state["calls"]) == 0, "no API call is attempted without a key")
        check("GEMINI_API_KEY" in str(exc), "the error tells the operator which variable to set")
    with_sdk(lambda i, kw: FakeResponse("x"), _t)


def test_timeout_is_not_retried():
    def _t(state):
        exc = raises(lambda: GeminiProvider(api_key="k").generate("s", "u"))
        check(isinstance(exc, LLMTimeoutError), f"timeout classified as LLMTimeoutError, got {type(exc).__name__}")
        check(len(state["calls"]) == 1, "a timeout is NOT retried (retrying would double the wait)")
    def behavior(i, kw):
        raise Exception("Deadline Exceeded: the request timed out")
    with_sdk(behavior, _t)


def test_rate_limit_retries_once_then_succeeds():
    def _t(state):
        reply = GeminiProvider(api_key="k").generate("s", "u")
        check(reply == "recovered", "a transient 429 followed by success returns the reply")
        check(len(state["calls"]) == 2, "exactly one retry was made")
    def behavior(i, kw):
        if i == 0:
            raise FakeAPIError(429, "RESOURCE_EXHAUSTED")
        return FakeResponse("recovered")
    with_sdk(behavior, _t)


def test_persistent_rate_limit_is_bounded():
    def _t(state):
        exc = raises(lambda: GeminiProvider(api_key="k").generate("s", "u"))
        check(isinstance(exc, LLMRateLimitError), "persistent 429 -> LLMRateLimitError")
        check(len(state["calls"]) == 1 + config.LLM_MAX_RETRIES, "retries are bounded by LLM_MAX_RETRIES (no infinite loop)")
    def behavior(i, kw):
        raise FakeAPIError(429, "RESOURCE_EXHAUSTED")
    with_sdk(behavior, _t)


def test_server_error_retried_once_then_api_error_without_leaking_message():
    def _t(state):
        exc = raises(lambda: GeminiProvider(api_key="k").generate("s", "u"))
        check(isinstance(exc, LLMAPIError) and not isinstance(exc, (LLMTimeoutError, LLMRateLimitError)), "500 -> LLMAPIError")
        check(len(state["calls"]) == 2, "a 5xx is retried exactly once")
        check("secret-detail" not in str(exc), "raw SDK error text is not echoed into our error")
    def behavior(i, kw):
        raise FakeAPIError(500, "secret-detail")
    with_sdk(behavior, _t)


def test_client_error_is_not_retried():
    def _t(state):
        exc = raises(lambda: GeminiProvider(api_key="k").generate("s", "u"))
        check(isinstance(exc, LLMAPIError), "404 (e.g. retired/unknown model) -> LLMAPIError")
        check(len(state["calls"]) == 1, "a 4xx other than 429 is not retried")
    def behavior(i, kw):
        raise FakeAPIError(404, "model not found")
    with_sdk(behavior, _t)


def test_empty_response():
    def _t(state):
        exc = raises(lambda: GeminiProvider(api_key="k").generate("s", "u"))
        check(isinstance(exc, EmptyResponseError), "blank text -> EmptyResponseError")
    with_sdk(lambda i, kw: FakeResponse("   "), _t)


def test_thinking_config_only_for_flash_25():
    def _t(state):
        GeminiProvider(api_key="k", model_name="gemini-2.5-pro").generate("s", "u")
        check(not hasattr(state["calls"][0]["config"], "thinking_config"), "thinking_config is not sent for non-Flash models (Pro can't disable thinking)")
    with_sdk(lambda i, kw: FakeResponse("x"), _t)


def run():
    for name, fn in sorted(globals().items()):
        if name.startswith("test_") and callable(fn):
            print(f"\n--- {name} ---")
            fn()
    print("-" * 60)
    if _failures == 0:
        print("ALL LLM PROVIDER MOCKING TESTS PASSED (fake google.genai injected via sys.modules - real network/package NOT used)")
    else:
        print(f"{_failures} TEST(S) FAILED")
        sys.exit(1)


if __name__ == "__main__":
    run()
