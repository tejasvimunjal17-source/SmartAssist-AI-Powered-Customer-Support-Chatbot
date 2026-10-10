"""
Tests app/response_generator.py end-to-end EXCEPT the real network call,
using a fake LLMProvider that implements the same interface as
GeminiProvider. This lets us genuinely test:
- successful response path
- every LLMError subtype triggering the fallback
- empty/irrelevant retrieval short-circuiting before any API call
- context construction (retrieved articles actually reach the prompt)
- prompt-injection-resistant structure (system vs. user prompt separation)

Run with:
    python -m tests.test_response_generator
"""

from app.llm_provider import (
    EmptyResponseError,
    LLMAPIError,
    LLMProvider,
    LLMRateLimitError,
    LLMTimeoutError,
    MissingAPIKeyError,
)
from app.response_generator import (
    FALLBACK_LLM_UNAVAILABLE,
    FALLBACK_NO_KNOWLEDGE,
    generate_response,
)
from app.retrieval import RetrievedArticle


def make_article(relevant=True, content="How to reset your password: go to settings..."):
    return RetrievedArticle(
        id="account-002",
        title="How do I reset my password?",
        category="account",
        content=content,
        source_filename="how-do-i-reset-my-password.md",
        source_path="/kb/account/how-do-i-reset-my-password.md",
        distance=0.3,
        is_relevant=relevant,
    )


class FakeProviderSuccess(LLMProvider):
    def __init__(self):
        self.received_system_prompt = None
        self.received_user_prompt = None

    def generate(self, system_prompt, user_prompt):
        self.received_system_prompt = system_prompt
        self.received_user_prompt = user_prompt
        return "To reset your password, go to Account Settings and click Forgot Password."


class FakeProviderRaises(LLMProvider):
    def __init__(self, exc):
        self.exc = exc

    def generate(self, system_prompt, user_prompt):
        raise self.exc


def run():
    failures = 0

    # --- Test 1: no relevant articles -> the LLM IS still asked (root-cause fix) ---
    class ProviderThatMustNotBeCalled(LLMProvider):
        def generate(self, system_prompt, user_prompt):
            raise AssertionError("provider.generate() should not be called for an empty query")

    class RecordingProvider(LLMProvider):
        def __init__(self):
            self.calls = 0
            self.user_prompt = None

        def generate(self, system_prompt, user_prompt):
            self.calls += 1
            self.user_prompt = user_prompt
            return "Paris."

    rec = RecordingProvider()
    result = generate_response("What is the capital of France?", [make_article(relevant=False)], provider=rec)
    if rec.calls != 1 or result.used_fallback or result.reply != "Paris." or result.articles_used != []:
        print("FAIL: with no relevant articles the LLM must still be called exactly once and its reply used")
        failures += 1
    elif "No matching help articles" not in rec.user_prompt:
        print("FAIL: the prompt should tell the model that no help article matched")
        failures += 1
    else:
        print("PASS: no relevant articles -> the LLM is still called once (general answer), used_fallback=False, no articles reported")

    # --- Test 2: empty query -> fallback ---
    result = generate_response("   ", [make_article()], provider=ProviderThatMustNotBeCalled())
    if not result.used_fallback:
        print("FAIL: expected fallback for empty query")
        failures += 1
    else:
        print("PASS: empty query -> fallback")

    # --- Test 3: successful path passes context and query into the prompt ---
    fake = FakeProviderSuccess()
    result = generate_response("I forgot my password", [make_article()], provider=fake)
    if result.used_fallback or "reset your password" not in result.reply.lower():
        print(f"FAIL: expected successful reply, got: {result}")
        failures += 1
    else:
        print("PASS: successful generation returns the provider's reply")

    if "account-002" not in result.articles_used:
        print("FAIL: expected articles_used to include the relevant article's id")
        failures += 1
    else:
        print("PASS: articles_used correctly tracks which article was included")

    if "reset your password" not in fake.received_user_prompt.lower():
        print("FAIL: retrieved article content did not reach the user prompt")
        failures += 1
    else:
        print("PASS: retrieved knowledge context reached the LLM prompt")

    if "I forgot my password" not in fake.received_user_prompt:
        print("FAIL: original user query was not preserved in the prompt")
        failures += 1
    else:
        print("PASS: original user question is preserved, not rewritten/lost")

    # --- Test 4: system prompt / user prompt are kept SEPARATE (injection defense) ---
    if fake.received_system_prompt is None or fake.received_system_prompt == fake.received_user_prompt:
        print("FAIL: system prompt and user prompt must be distinct, separately-passed strings")
        failures += 1
    else:
        print("PASS: system prompt and user/context prompt are passed as separate strings")

    if "not instructions" not in fake.received_system_prompt.lower() and "never instructions" not in fake.received_system_prompt.lower():
        print("FAIL: system prompt should explicitly instruct the model to treat context as data, not commands")
        failures += 1
    else:
        print("PASS: system prompt explicitly tells the model to treat KB/user content as data, not instructions")

    # --- Test 5: every LLMError subtype triggers the graceful fallback ---
    failures_before = failures
    for exc in [MissingAPIKeyError("no key"), LLMTimeoutError("timeout"), LLMRateLimitError("rate limited"), LLMAPIError("api down"), EmptyResponseError("empty")]:
        result = generate_response("I forgot my password", [make_article()], provider=FakeProviderRaises(exc))
        expected_reason = {
            "MissingAPIKeyError": "missing_api_key", "LLMTimeoutError": "llm_timeout",
            "LLMRateLimitError": "llm_rate_limited", "LLMAPIError": "llm_error", "EmptyResponseError": "llm_error",
        }[type(exc).__name__]
        # A relevant article exists, so the fallback shows ITS text (and reports used_fallback=True).
        if (not result.used_fallback or result.fallback_reason != expected_reason
                or not result.reply.strip() or result.articles_used != ["account-002"]):
            print(f"FAIL: {type(exc).__name__} did not trigger the graceful fallback with reason {expected_reason!r}: {result}")
            failures += 1
    if failures == failures_before:
        print("PASS: every LLMError subtype (missing key, timeout, rate limit, API error, empty response) triggers the fallback")

    # --- Test 6: context size limits are respected (long content gets truncated) ---
    long_content = "x" * 5000
    fake2 = FakeProviderSuccess()
    generate_response("test", [make_article(content=long_content)], provider=fake2)
    if long_content in fake2.received_user_prompt:
        print("FAIL: article content should have been truncated (MAX_CHARS_PER_ARTICLE), but full text was sent")
        failures += 1
    else:
        print("PASS: long article content is truncated before being sent to the LLM")

    # --- Test 7 (Day 15): conversation_history is optional and backward compatible ---
    fake_no_history = FakeProviderSuccess()
    generate_response("I forgot my password", [make_article()], provider=fake_no_history)
    if "CONVERSATION HISTORY" in fake_no_history.received_user_prompt:
        print("FAIL: omitting conversation_history should produce the exact pre-Day-15 prompt shape, with no history section at all")
        failures += 1
    else:
        print("PASS: omitting conversation_history (the default) produces the unchanged, pre-Day-15 prompt shape")

    # --- Test 8 (Day 15): conversation_history, when provided, reaches the prompt ---
    fake_history = FakeProviderSuccess()
    history = [
        {"role": "user", "content": "I can't access my account"},
        {"role": "assistant", "content": "Can you tell me more about the error?"},
    ]
    generate_response("it still doesn't work", [make_article()], provider=fake_history, conversation_history=history)
    if "CONVERSATION HISTORY" not in fake_history.received_user_prompt or "I can't access my account" not in fake_history.received_user_prompt:
        print("FAIL: provided conversation_history should appear in the prompt, clearly delimited")
        failures += 1
    else:
        print("PASS: conversation_history, when provided, reaches the prompt in a clearly delimited section")

    # --- Test 9 (Day 15): conversation_history is capped, not sent in full ---
    long_history = [{"role": "user", "content": f"message {i}"} for i in range(20)]
    fake_long_history = FakeProviderSuccess()
    generate_response("current question", [make_article()], provider=fake_long_history, conversation_history=long_history)
    if "message 0" in fake_long_history.received_user_prompt:
        print("FAIL: conversation_history should be capped to the most recent MAX_HISTORY_MESSAGES_IN_PROMPT turns, but an old message leaked through")
        failures += 1
    elif "message 19" not in fake_long_history.received_user_prompt:
        print("FAIL: the most recent history turn should be included")
        failures += 1
    else:
        print("PASS: conversation_history is capped to the most recent turns, not sent in full")

    # --- Test 10 (Day 15): empty conversation_history behaves like None ---
    fake_empty_history = FakeProviderSuccess()
    generate_response("I forgot my password", [make_article()], provider=fake_empty_history, conversation_history=[])
    if "CONVERSATION HISTORY" in fake_empty_history.received_user_prompt:
        print("FAIL: an empty conversation_history list should also omit the history section, same as None")
        failures += 1
    else:
        print("PASS: an empty conversation_history list ([]) behaves identically to omitting it (None)")

    print("-" * 60)
    if failures == 0:
        print("ALL RESPONSE GENERATOR TESTS PASSED (real Gemini network call NOT attempted)")
    else:
        print(f"{failures} TEST(S) FAILED")


if __name__ == "__main__":
    run()
