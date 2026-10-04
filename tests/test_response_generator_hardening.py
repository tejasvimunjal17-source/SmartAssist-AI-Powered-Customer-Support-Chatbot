"""
Day 14 regression tests for three genuine bugs found during the Day 14
audit and fixed in app/response_generator.py:

1. A provider raising any exception OTHER than an LLMError subtype used
   to propagate uncaught, crashing the whole response pipeline instead
   of degrading to the safe fallback message.
2. A provider that returned None, an empty string, or a non-string value
   (instead of raising EmptyResponseError) was passed straight through
   as if it were a valid reply.
3. Calling generate_response(query, None) — i.e. no retrieved articles
   list at all, not just an empty one — crashed with an uncaught
   TypeError ("'NoneType' object is not iterable").

Each bug was reproduced BEFORE being fixed (see the Day 14 summary for
the exact repro commands and output). These tests exist so the bugs
cannot silently come back.

Run with:
    python -m tests.test_response_generator_hardening
"""

from app.llm_provider import LLMProvider
from app.response_generator import FALLBACK_LLM_UNAVAILABLE, FALLBACK_NO_KNOWLEDGE, generate_response
from app.retrieval import RetrievedArticle

_failures = 0


def check(condition, description):
    global _failures
    status = "PASS" if condition else "FAIL"
    print(f"[{status}] {description}")
    if not condition:
        _failures += 1


def article():
    return RetrievedArticle(
        id="a", title="t", category="c", content="Some helpful content.",
        source_filename="f.md", source_path="/kb/f.md", distance=0.1, is_relevant=True,
    )


class RaisesValueError(LLMProvider):
    """Simulates a provider/SDK bug that raises something other than a documented LLMError subtype."""
    def generate(self, system_prompt, user_prompt):
        raise ValueError("some unexpected internal SDK error, not an LLMError")


class RaisesKeyError(LLMProvider):
    def generate(self, system_prompt, user_prompt):
        raise KeyError("unexpected_field")


class ReturnsNone(LLMProvider):
    def generate(self, system_prompt, user_prompt):
        return None


class ReturnsEmptyString(LLMProvider):
    def generate(self, system_prompt, user_prompt):
        return "   "  # whitespace-only


class ReturnsWrongType(LLMProvider):
    def generate(self, system_prompt, user_prompt):
        return {"text": "a dict, not a string — e.g. a raw unparsed SDK response object"}


class ReturnsValidReply(LLMProvider):
    """Control case: a well-behaved provider must still work normally after the hardening fix."""
    def generate(self, system_prompt, user_prompt):
        return "A normal, valid reply."


def run():
    print("--- Bug 1: arbitrary (non-LLMError) provider exceptions no longer crash the pipeline ---")
    for name, provider in [("ValueError", RaisesValueError()), ("KeyError", RaisesKeyError())]:
        result = generate_response("question", [article()], provider=provider)
        check(
            result.used_fallback is True and result.reply == FALLBACK_LLM_UNAVAILABLE,
            f"a provider raising a plain {name} degrades to the safe fallback instead of crashing generate_response",
        )

    print("\n--- Bug 2: malformed (successful but invalid) provider return values are treated as failures ---")
    for name, provider in [("None", ReturnsNone()), ("whitespace-only string", ReturnsEmptyString()), ("non-string (dict)", ReturnsWrongType())]:
        result = generate_response("question", [article()], provider=provider)
        check(
            result.used_fallback is True and result.reply == FALLBACK_LLM_UNAVAILABLE,
            f"a provider returning {name} (no exception raised) is treated as a malformed response, not passed through",
        )

    print("\n--- Control: a well-behaved provider is unaffected by the hardening ---")
    result = generate_response("question", [article()], provider=ReturnsValidReply())
    check(
        result.used_fallback is False and result.reply == "A normal, valid reply.",
        "a provider that behaves correctly still returns its real reply, not a fallback",
    )

    print("\n--- Bug 3: retrieved_articles=None no longer crashes with TypeError ---")
    result = generate_response("question", None)
    check(
        result.used_fallback is True and result.reply == FALLBACK_NO_KNOWLEDGE,
        "generate_response(query, None) degrades to the no-knowledge fallback instead of raising TypeError",
    )

    # And the empty-list case (already the documented behavior) still works identically.
    result_empty_list = generate_response("question", [])
    check(result.reply == result_empty_list.reply, "None and [] for retrieved_articles produce the identical, expected fallback")

    print("-" * 60)
    if _failures == 0:
        print("ALL RESPONSE GENERATOR HARDENING TESTS PASSED (regression coverage for 3 bugs found and fixed on Day 14)")
    else:
        print(f"{_failures} TEST(S) FAILED")


if __name__ == "__main__":
    run()
