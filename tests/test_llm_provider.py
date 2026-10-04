"""
Tests app/llm_provider.py's error-handling logic WITHOUT a real API key
or network access, by testing MissingAPIKeyError directly (no google
SDK call needed for that path) and by monkeypatching the module-level
`_get_model`/`generate` behavior isn't attempted here for the SDK-call
path — that needs the real google-generativeai package installed, which
is not available in this sandbox. See tests/test_response_generator.py
for the fuller set of mocked success/failure scenarios, which patch at
the LLMProvider interface level instead (more robust, less coupled to
the SDK's internals).

Run with:
    python -m tests.test_llm_provider
"""

from app.llm_provider import GeminiProvider, MissingAPIKeyError


def run():
    failures = 0

    # --- Test: missing API key raises MissingAPIKeyError, doesn't crash ---
    provider = GeminiProvider(api_key=None)
    try:
        provider.generate("system prompt", "user prompt")
        print("FAIL: expected MissingAPIKeyError when no API key is configured")
        failures += 1
    except MissingAPIKeyError:
        print("PASS: missing API key raises MissingAPIKeyError as expected")
    except Exception as exc:
        print(f"FAIL: expected MissingAPIKeyError, got {type(exc).__name__}: {exc}")
        failures += 1

    # --- Test: an explicit (fake) API key is accepted at construction time ---
    provider_with_key = GeminiProvider(api_key="fake-key-for-testing")
    if provider_with_key.api_key != "fake-key-for-testing":
        print("FAIL: provider did not store the given api_key")
        failures += 1
    else:
        print("PASS: provider correctly stores a given api_key")

    print("-" * 60)
    if failures == 0:
        print("ALL LLM PROVIDER TESTS PASSED (real Gemini network call NOT attempted)")
    else:
        print(f"{failures} TEST(S) FAILED")


if __name__ == "__main__":
    run()
