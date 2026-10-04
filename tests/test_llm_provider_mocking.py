"""
Day 14 API mocking tests for app/llm_provider.py's GeminiProvider.

WHY THIS FILE EXISTS: the existing Day 6 tests (tests/test_llm_provider.py,
tests/test_response_generator.py) mock at the LLMProvider INTERFACE
boundary — a FakeProvider stands in for GeminiProvider entirely. That's
correct for testing response_generator.py, but it means GeminiProvider's
OWN code (genai.configure, GenerativeModel construction, the
timeout/rate-limit/error message classification in generate()) has never
actually run in any test.

Because llm_provider.py imports `google.generativeai` LAZILY (inside
methods, not at module load time), we can inject a FAKE
google.generativeai module into sys.modules before calling
GeminiProvider.generate(). This exercises GeminiProvider's REAL code —
its actual configure()/GenerativeModel() calls and its actual exception
classification logic — using no network access and no real package
installed. This is "mocking at the appropriate provider boundary" per
the Day 14 brief, one level more real than the existing interface-level
mocks.

Run with:
    python -m tests.test_llm_provider_mocking
"""

import sys
import types
from unittest import mock

from app.llm_provider import (
    EmptyResponseError,
    GeminiProvider,
    LLMAPIError,
    LLMRateLimitError,
    LLMTimeoutError,
)

_failures = 0


def check(condition, description):
    global _failures
    status = "PASS" if condition else "FAIL"
    print(f"[{status}] {description}")
    if not condition:
        _failures += 1


def make_fake_genai(behavior):
    """
    Builds a fake `google.generativeai` module. `behavior` is a callable
    that receives (model_name, system_instruction, prompt, request_options)
    and either returns a fake response object (with a .text attribute)
    or raises an exception — exactly like the real SDK would.
    """
    fake_genai = types.ModuleType("google.generativeai")
    calls = {"configure_args": None, "generate_content_args": []}

    def configure(api_key=None):
        calls["configure_args"] = {"api_key": api_key}

    class FakeGenerativeModel:
        def __init__(self, model_name=None, system_instruction=None):
            self.model_name = model_name
            self.system_instruction = system_instruction

        def generate_content(self, prompt, request_options=None):
            calls["generate_content_args"].append(
                {
                    "model_name": self.model_name,
                    "system_instruction": self.system_instruction,
                    "prompt": prompt,
                    "request_options": request_options,
                }
            )
            return behavior(self.model_name, self.system_instruction, prompt, request_options)

    fake_genai.configure = configure
    fake_genai.GenerativeModel = FakeGenerativeModel
    return fake_genai, calls


def with_fake_genai(behavior, test_fn):
    fake_genai, calls = make_fake_genai(behavior)
    fake_google = types.ModuleType("google")
    with mock.patch.dict(sys.modules, {"google": fake_google, "google.generativeai": fake_genai}):
        test_fn(calls)


class FakeResponse:
    def __init__(self, text):
        self.text = text


def test_successful_response_uses_the_real_provider_code_path():
    def behavior(model_name, system_instruction, prompt, request_options):
        return FakeResponse("A real successful reply from the (fake) Gemini SDK.")

    def _test(calls):
        provider = GeminiProvider(api_key="fake-test-key")
        reply = provider.generate("system rules", "what is my order status?")

        check(reply == "A real successful reply from the (fake) Gemini SDK.", "GeminiProvider.generate() returns the SDK's text on success")
        check(calls["configure_args"] == {"api_key": "fake-test-key"}, "genai.configure() was actually called with our api_key (real provider code ran, not a stand-in)")
        check(len(calls["generate_content_args"]) == 1, "generate_content was called exactly once")
        call = calls["generate_content_args"][0]
        check(call["system_instruction"] == "system rules", "the system prompt reaches GenerativeModel's system_instruction parameter, kept separate from the user prompt")
        check(call["prompt"] == "what is my order status?", "the user prompt is passed as the content argument, not merged with the system prompt")
        check(call["request_options"] == {"timeout": 10}, "the configured request timeout (10s, from app.config.LLM_REQUEST_TIMEOUT_SECONDS) is actually passed to the SDK call")

    with_fake_genai(behavior, _test)


def test_timeout_classified_correctly():
    def behavior(model_name, system_instruction, prompt, request_options):
        raise Exception("Deadline Exceeded: the request timed out after 10s")

    def _test(calls):
        provider = GeminiProvider(api_key="fake-test-key")
        raised = None
        try:
            provider.generate("s", "u")
        except Exception as e:
            raised = e
        check(isinstance(raised, LLMTimeoutError), f"a real SDK exception mentioning 'timed out'/'deadline' is classified as LLMTimeoutError, got {type(raised).__name__}")

    with_fake_genai(behavior, _test)


def test_rate_limit_classified_correctly():
    for message in ["429 Too Many Requests", "You have exceeded your current quota", "Rate limit reached, please retry later"]:
        def behavior(model_name, system_instruction, prompt, request_options, _msg=message):
            raise Exception(_msg)

        def _test(calls, _msg=message):
            provider = GeminiProvider(api_key="fake-test-key")
            raised = None
            try:
                provider.generate("s", "u")
            except Exception as e:
                raised = e
            check(isinstance(raised, LLMRateLimitError), f"SDK error {_msg!r} is classified as LLMRateLimitError, got {type(raised).__name__ if raised else None}")

        with_fake_genai(behavior, _test)


def test_generic_sdk_error_classified_as_api_error():
    def behavior(model_name, system_instruction, prompt, request_options):
        raise Exception("500 Internal Server Error")

    def _test(calls):
        provider = GeminiProvider(api_key="fake-test-key")
        raised = None
        try:
            provider.generate("s", "u")
        except Exception as e:
            raised = e
        check(isinstance(raised, LLMAPIError), f"an SDK error that isn't a timeout or rate-limit message is classified as the generic LLMAPIError, got {type(raised).__name__ if raised else None}")
        check(not isinstance(raised, (LLMTimeoutError, LLMRateLimitError)), "and specifically NOT misclassified as timeout or rate-limit")

    with_fake_genai(behavior, _test)


def test_empty_response_from_real_sdk_shape_raises_empty_response_error():
    for empty_text in ["", "   ", None]:
        def behavior(model_name, system_instruction, prompt, request_options, _text=empty_text):
            return FakeResponse(_text)

        def _test(calls, _text=empty_text):
            provider = GeminiProvider(api_key="fake-test-key")
            raised = None
            try:
                provider.generate("s", "u")
            except Exception as e:
                raised = e
            check(isinstance(raised, EmptyResponseError), f"an SDK response with .text={_text!r} raises EmptyResponseError, got {type(raised).__name__ if raised else None}")

        with_fake_genai(behavior, _test)


def test_network_style_exception_classified_as_api_error_not_crash():
    def behavior(model_name, system_instruction, prompt, request_options):
        raise ConnectionError("Failed to establish a new connection: [Errno -2] Name or service not known")

    def _test(calls):
        provider = GeminiProvider(api_key="fake-test-key")
        raised = None
        try:
            provider.generate("s", "u")
        except Exception as e:
            raised = e
        check(isinstance(raised, LLMAPIError), f"a real network-style ConnectionError is caught and wrapped as LLMAPIError (never propagates as a raw ConnectionError), got {type(raised).__name__ if raised else None}")

    with_fake_genai(behavior, _test)


def test_full_pipeline_with_mocked_sdk_success_and_failure():
    """Ties the fake SDK all the way through response_generator.generate_response(), the real end-to-end path a wired /chat would use."""
    from app.response_generator import FALLBACK_LLM_UNAVAILABLE, generate_response
    from app.retrieval import RetrievedArticle

    article = RetrievedArticle(
        id="a", title="Reset password", category="account", content="Go to settings and click reset.",
        source_filename="f.md", source_path="/kb/f.md", distance=0.1, is_relevant=True,
    )

    def success_behavior(model_name, system_instruction, prompt, request_options):
        return FakeResponse("To reset your password, go to Settings.")

    def _test_success(calls):
        provider = GeminiProvider(api_key="fake-test-key")
        result = generate_response("how do I reset my password", [article], provider=provider)
        check(result.used_fallback is False and "Settings" in result.reply,
              "end-to-end: a mocked successful Gemini call flows all the way through response_generator to a real (non-fallback) reply")

    with_fake_genai(success_behavior, _test_success)

    def failure_behavior(model_name, system_instruction, prompt, request_options):
        raise Exception("503 Service Unavailable")

    def _test_failure(calls):
        provider = GeminiProvider(api_key="fake-test-key")
        result = generate_response("how do I reset my password", [article], provider=provider)
        check(result.used_fallback is True and result.reply == FALLBACK_LLM_UNAVAILABLE,
              "end-to-end: a mocked Gemini failure flows through to the same safe fallback the customer would see")

    with_fake_genai(failure_behavior, _test_failure)


def run():
    tests = [
        test_successful_response_uses_the_real_provider_code_path,
        test_timeout_classified_correctly,
        test_rate_limit_classified_correctly,
        test_generic_sdk_error_classified_as_api_error,
        test_empty_response_from_real_sdk_shape_raises_empty_response_error,
        test_network_style_exception_classified_as_api_error_not_crash,
        test_full_pipeline_with_mocked_sdk_success_and_failure,
    ]
    for test in tests:
        print(f"\n--- {test.__name__} ---")
        test()

    print("\n" + "-" * 60)
    if _failures == 0:
        print("ALL LLM PROVIDER MOCKING TESTS PASSED (fake google.generativeai injected via sys.modules — real network/package NOT used)")
    else:
        print(f"{_failures} TEST(S) FAILED")


if __name__ == "__main__":
    run()
