"""
Day 15 integration tests for app/chat_orchestrator.py — the module that
finally wires preprocessing, intent routing, retrieval, conversation
history, LLM generation, escalation, and persistence together into the
real /chat pipeline.

HONESTY NOTE: FastAPI is not installed in this sandbox (no internet
access), so these tests call handle_chat_message() directly rather than
through an HTTP request — exactly the function app/main.py's /chat route
now calls. This tests the real integration of every module EXCEPT
FastAPI's own routing/request-validation layer, which is documented as a
sandbox limitation, not silently skipped.

All external boundaries (the embedding model, the vector store, the
Gemini provider) use real temp SQLite plus fakes, never the real network
— consistent with every prior day's testing approach in this project.

Run with:
    python -m tests.test_chat_orchestrator
"""

import os
import shutil
import tempfile

from app.chat_orchestrator import ESCALATION_REPLY, FRUSTRATION_REPLY, GREETING_REPLY, handle_chat_message
from app.conversation_memory import get_recent_history, init_db
from app.feedback import submit_feedback
from app.llm_provider import LLMAPIError, LLMProvider
from app.response_generator import FALLBACK_LLM_UNAVAILABLE, FALLBACK_NO_KNOWLEDGE

_failures = 0


def check(condition, description):
    global _failures
    status = "PASS" if condition else "FAIL"
    print(f"[{status}] {description}")
    if not condition:
        _failures += 1


def with_temp_db(test_fn):
    tmp_dir = tempfile.mkdtemp()
    db_path = os.path.join(tmp_dir, "test_conversations.db")
    try:
        init_db(db_path=db_path)
        test_fn(db_path)
    finally:
        shutil.rmtree(tmp_dir, ignore_errors=True)


def fake_embed(texts):
    return [[0.1, 0.2, 0.3] for _ in texts]


class SuccessProvider(LLMProvider):
    def __init__(self, text="To reset your password, go to Account Settings and click Forgot Password."):
        self.text = text
        self.last_system_prompt = None
        self.last_user_prompt = None

    def generate(self, system_prompt, user_prompt):
        self.last_system_prompt = system_prompt
        self.last_user_prompt = user_prompt
        return self.text


class FailingProvider(LLMProvider):
    def generate(self, system_prompt, user_prompt):
        raise LLMAPIError("simulated Gemini outage")


class ArticleCollection:
    """A fake vector-store collection that always returns one relevant 'reset password' article."""
    def query(self, query_embeddings, n_results, include):
        return {
            "ids": [["account-001"]],
            "documents": [["To reset your password, go to Settings and click Forgot Password."]],
            "metadatas": [[{"title": "Reset password", "category": "account", "source_filename": "f.md", "source_path": "/kb/f.md"}]],
            "distances": [[0.2]],
        }


class EmptyCollection:
    """A fake vector-store collection that never finds anything relevant."""
    def query(self, query_embeddings, n_results, include):
        return {"ids": [[]], "documents": [[]], "metadatas": [[]], "distances": [[]]}


def test_normal_chat_with_retrieval_and_llm():
    def _test(db_path):
        provider = SuccessProvider()
        result = handle_chat_message(
            "how do I reset my password", db_path=db_path, provider=provider,
            embed_fn=fake_embed, collection=ArticleCollection(),
        )
        check(bool(result.session_id), "a new session is created for a fresh conversation")
        check(result.reply == provider.text, "the real LLM-generated reply (via the fake provider) is returned")
        check(result.used_llm is True, "used_llm is True when the LLM actually produced the reply")
        check(result.message_id is not None, "a real assistant message_id is returned")
        check(result.articles_used == ["account-001"], "the retrieved article that informed the answer is reported")
        check(result.escalated is False, "a normal support question does not escalate")

    with_temp_db(_test)


def test_recognized_intent_is_reported():
    def _test(db_path):
        result = handle_chat_message(
            "how do I reset my password", db_path=db_path, provider=SuccessProvider(),
            embed_fn=fake_embed, collection=ArticleCollection(),
        )
        check(result.intent == "faq", "the Day 7 intent router's classification is surfaced in the result")
        check(result.intent_confidence is not None and result.intent_confidence > 0, "a real confidence value accompanies the intent")

    with_temp_db(_test)


def test_useful_retrieval_feeds_the_llm_prompt():
    def _test(db_path):
        provider = SuccessProvider()
        handle_chat_message(
            "how do I reset my password", db_path=db_path, provider=provider,
            embed_fn=fake_embed, collection=ArticleCollection(),
        )
        check(provider.last_user_prompt is not None and "Reset password" in provider.last_user_prompt,
              "the retrieved article's content actually reached the LLM prompt")

    with_temp_db(_test)


def test_no_useful_retrieval_still_asks_the_llm():
    def _test(db_path):
        provider = SuccessProvider(text="About 11 metres per second, roughly.")
        result = handle_chat_message(
            "what is the airspeed velocity of an unladen swallow", db_path=db_path, provider=provider,
            embed_fn=fake_embed, collection=EmptyCollection(),
        )
        check(result.reply == "About 11 metres per second, roughly.", "with no relevant articles the LLM's answer is still returned (no canned reply)")
        check(result.used_llm is True, "used_llm is True because the LLM really was called")
        check(result.articles_used == [], "no articles are reported as used")
        check(result.escalated is False, "an unmatched question is NOT escalated")
        check("No matching help articles" in provider.last_user_prompt, "the model is told that no help article matched")

    with_temp_db(_test)


def test_llm_failure_degrades_to_safe_fallback():
    def _test(db_path):
        result = handle_chat_message(
            "how do I reset my password", db_path=db_path, provider=FailingProvider(),
            embed_fn=fake_embed, collection=ArticleCollection(),
        )
        check("Reset password" in result.reply and result.fallback_reason == "llm_error",
              "a real LLM provider failure degrades to the relevant help article's text with a reason code, not a crash")
        check(result.escalated is False, "an LLM outage never turns into an escalation")
        check(result.used_llm is False, "used_llm is False when the fallback was used")
        check(result.message_id is not None, "the conversation still persists even when the LLM fails")

    with_temp_db(_test)


def test_conversation_history_reaches_the_llm():
    def _test(db_path):
        provider = SuccessProvider()
        r1 = handle_chat_message("I can't access my account", db_path=db_path, provider=provider, embed_fn=fake_embed, collection=ArticleCollection())
        provider2 = SuccessProvider()
        handle_chat_message("it still doesn't work", r1.session_id, db_path=db_path, provider=provider2, embed_fn=fake_embed, collection=ArticleCollection())

        check(provider2.last_user_prompt is not None and "I can't access my account" in provider2.last_user_prompt,
              "the first turn's message appears in the SECOND turn's prompt as conversation history")
        check("CONVERSATION HISTORY" in provider2.last_user_prompt, "the history is clearly delimited, per response_generator's Day 15 format")

    with_temp_db(_test)


def test_conversation_history_and_current_retrieval_are_distinguishable():
    """
    Motivated by a bug found while writing the Day 15 demo transcript: a
    naive consumer of the prompt (there, a demo-only fake LLM; here, a
    test) could get confused if conversation history and the CURRENT
    turn's retrieved knowledge weren't clearly separated. That bug was
    in the demo script, not the app — this test confirms the REAL
    response_generator prompt structure keeps them genuinely separable:
    the history block comes first, then a distinct KNOWLEDGE CONTEXT
    section for the CURRENT retrieval only.
    """
    def _test(db_path):
        class ArticleACollection:
            def query(self, query_embeddings, n_results, include):
                return {"ids": [["article-a"]], "documents": [["Content about topic A."]],
                        "metadatas": [[{"title": "Topic A Article", "category": "a", "source_filename": "a.md", "source_path": "/a.md"}]],
                        "distances": [[0.1]]}

        class ArticleBCollection:
            def query(self, query_embeddings, n_results, include):
                return {"ids": [["article-b"]], "documents": [["Content about topic B."]],
                        "metadatas": [[{"title": "Topic B Article", "category": "b", "source_filename": "b.md", "source_path": "/b.md"}]],
                        "distances": [[0.1]]}

        r1 = handle_chat_message("tell me about topic A", db_path=db_path, provider=SuccessProvider(), embed_fn=fake_embed, collection=ArticleACollection())
        provider2 = SuccessProvider()
        handle_chat_message("now tell me about topic B", r1.session_id, db_path=db_path, provider=provider2, embed_fn=fake_embed, collection=ArticleBCollection())

        prompt = provider2.last_user_prompt
        history_part, _, rest = prompt.partition("KNOWLEDGE CONTEXT:")
        knowledge_part, _, customer_part = rest.partition("CUSTOMER MESSAGE")

        check("Topic A Article" in history_part or "tell me about topic A" in history_part,
              "the PRIOR turn (topic A) appears in the history section, before KNOWLEDGE CONTEXT")
        check("Topic B Article" in knowledge_part and "Topic A Article" not in knowledge_part,
              "the KNOWLEDGE CONTEXT section contains ONLY the current turn's retrieved article (Topic B), not the prior one")
        check("now tell me about topic B" in customer_part,
              "the CUSTOMER MESSAGE section contains only the current question")

    with_temp_db(_test)


def test_conversation_history_excludes_the_current_message():
    def _test(db_path):
        provider = SuccessProvider()
        handle_chat_message("how do I reset my password", db_path=db_path, provider=provider, embed_fn=fake_embed, collection=ArticleCollection())
        check("CONVERSATION HISTORY" not in provider.last_user_prompt,
              "the very first message in a session has no prior history, so no history section appears at all")

    with_temp_db(_test)


def test_escalation_skips_retrieval_and_llm():
    def _test(db_path):
        provider = SuccessProvider()

        class MustNotBeCalled:
            def query(self, *a, **k):
                raise AssertionError("retrieval must not run when escalating")

        result = handle_chat_message(
            "This is ridiculous, it's still not working!! I need this fixed",
            db_path=db_path, provider=provider, embed_fn=fake_embed, collection=MustNotBeCalled(),
        )
        check(result.escalated is True, "clear frustration signals trigger escalation")
        check(result.reply == FRUSTRATION_REPLY, "the fixed, honest frustration reply is returned")
        check("haven't notified" in result.reply and result.handoff_confirmed is False,
              "the reply never claims a human was contacted, and handoff_confirmed is False")
        check(provider.last_user_prompt is None, "the LLM is never called when escalating (retrieval/LLM are skipped entirely)")

    with_temp_db(_test)


def test_explicit_human_request_escalates_with_correct_reason():
    def _test(db_path):
        result = handle_chat_message("I want to talk to a human", db_path=db_path, provider=SuccessProvider(), embed_fn=fake_embed, collection=ArticleCollection())
        check(result.escalated is True and result.escalation_reason == "explicit_human_request",
              "an explicit human request escalates with the specific, correct reason")

    with_temp_db(_test)


def test_repeated_failure_across_turns_escalates():
    def _test(db_path):
        session_id = None
        for msg in ["the app is not working", "it is still broken, nothing changed"]:
            r = handle_chat_message(msg, session_id, db_path=db_path, provider=SuccessProvider(), embed_fn=fake_embed, collection=ArticleCollection())
            session_id = r.session_id
        final = handle_chat_message("still not working", session_id, db_path=db_path, provider=SuccessProvider(), embed_fn=fake_embed, collection=ArticleCollection())
        check(final.escalated is True, "repeated failure language across multiple turns triggers conversation-aware escalation (Day 9)")

    with_temp_db(_test)


def test_greeting_skips_retrieval_and_llm():
    def _test(db_path):
        class MustNotBeCalled:
            def query(self, *a, **k):
                raise AssertionError("retrieval must not run for a confidently rule-matched greeting")

        result = handle_chat_message("hi there", db_path=db_path, provider=SuccessProvider(), embed_fn=fake_embed, collection=MustNotBeCalled())
        check(result.reply == GREETING_REPLY, "a confident rule-matched greeting gets the short scripted reply")
        check(result.intent == "greeting", "intent is still correctly reported even when RAG/LLM are skipped")

    with_temp_db(_test)


def test_empty_and_whitespace_message():
    def _test(db_path):
        result = handle_chat_message("", db_path=db_path, provider=SuccessProvider())
        check(result.message_id is None, "an empty message produces no message_id (nothing stored)")
        check(get_recent_history(result.session_id, db_path=db_path) == [], "nothing is persisted for an empty message")

        result2 = handle_chat_message("   \n\t  ", db_path=db_path, provider=SuccessProvider())
        check(result2.message_id is None, "a whitespace-only message is treated the same as empty")

    with_temp_db(_test)


def test_none_session_id_and_invalid_session_id_both_start_fresh():
    def _test(db_path):
        r1 = handle_chat_message("hello", None, db_path=db_path, provider=SuccessProvider())
        r2 = handle_chat_message("hello", "a-session-id-that-was-never-created", db_path=db_path, provider=SuccessProvider())
        check(r1.session_id != r2.session_id, "both None and an unknown session_id correctly result in a (different) new session each time")

    with_temp_db(_test)


def test_very_long_message_handled():
    def _test(db_path):
        long_message = "how do I reset my password " * 500
        result = handle_chat_message(long_message, db_path=db_path, provider=SuccessProvider(), embed_fn=fake_embed, collection=ArticleCollection())
        check(result.message_id is not None, "a very long message (~14,500 characters) is processed without crashing")

    with_temp_db(_test)


def test_sql_injection_style_message_handled_safely():
    def _test(db_path):
        malicious = "'); DROP TABLE messages; --"
        result = handle_chat_message(malicious, db_path=db_path, provider=SuccessProvider(), embed_fn=fake_embed, collection=EmptyCollection())
        check(result.message_id is not None, "SQL-injection-style message content is handled safely (parameterized queries)")
        history = get_recent_history(result.session_id, db_path=db_path)
        check(history[0]["content"] == malicious, "the malicious-looking text is stored as inert plain data, unmodified")

    with_temp_db(_test)


def test_history_endpoint_compatibility():
    def _test(db_path):
        r1 = handle_chat_message("how do I reset my password", db_path=db_path, provider=SuccessProvider(), embed_fn=fake_embed, collection=ArticleCollection())
        handle_chat_message("thanks", r1.session_id, db_path=db_path, provider=SuccessProvider(), embed_fn=fake_embed, collection=EmptyCollection())

        history = get_recent_history(r1.session_id, db_path=db_path)
        check(len(history) == 4, "both turns (user+assistant each) are visible via the same get_recent_history() /history uses")
        check(all("id" in m and "role" in m and "content" in m and "timestamp" in m for m in history),
              "each history entry still has the exact shape /history's HistoryMessage model expects (Day 12's additive 'id' field included)")

    with_temp_db(_test)


def test_feedback_compatibility_with_integrated_chat():
    def _test(db_path):
        result = handle_chat_message("how do I reset my password", db_path=db_path, provider=SuccessProvider(), embed_fn=fake_embed, collection=ArticleCollection())
        feedback_result = submit_feedback(result.session_id, result.message_id, "helpful", db_path=db_path)
        check(feedback_result["updated"] is False, "feedback can still be submitted against a real assistant message produced by the integrated pipeline")

        greeting_result = handle_chat_message("hi", result.session_id, db_path=db_path, provider=SuccessProvider(), embed_fn=fake_embed, collection=ArticleCollection())
        feedback_on_greeting = submit_feedback(greeting_result.session_id, greeting_result.message_id, "not_helpful", db_path=db_path)
        check(feedback_on_greeting["updated"] is False, "a scripted greeting/escalation reply is a real stored assistant message and can also receive feedback")

    with_temp_db(_test)


def test_result_schema_has_all_expected_fields():
    def _test(db_path):
        result = handle_chat_message("how do I reset my password", db_path=db_path, provider=SuccessProvider(), embed_fn=fake_embed, collection=ArticleCollection())
        for field in ["reply", "session_id", "message_id", "intent", "intent_confidence", "escalated", "escalation_reason", "articles_used", "used_llm", "preprocessing_available"]:
            check(hasattr(result, field), f"ChatResult has the '{field}' field main.py's ChatResponse maps from")
        check(isinstance(result.reply, str) and isinstance(result.session_id, str), "reply and session_id are always strings, matching the Day 1-14 API contract types")
        check(isinstance(result.articles_used, list), "articles_used is always a list (never None), safe for a frontend to .map() over directly")

    with_temp_db(_test)


def run():
    tests = [
        test_normal_chat_with_retrieval_and_llm,
        test_recognized_intent_is_reported,
        test_useful_retrieval_feeds_the_llm_prompt,
        test_no_useful_retrieval_still_asks_the_llm,
        test_llm_failure_degrades_to_safe_fallback,
        test_conversation_history_reaches_the_llm,
        test_conversation_history_and_current_retrieval_are_distinguishable,
        test_conversation_history_excludes_the_current_message,
        test_escalation_skips_retrieval_and_llm,
        test_explicit_human_request_escalates_with_correct_reason,
        test_repeated_failure_across_turns_escalates,
        test_greeting_skips_retrieval_and_llm,
        test_empty_and_whitespace_message,
        test_none_session_id_and_invalid_session_id_both_start_fresh,
        test_very_long_message_handled,
        test_sql_injection_style_message_handled_safely,
        test_history_endpoint_compatibility,
        test_feedback_compatibility_with_integrated_chat,
        test_result_schema_has_all_expected_fields,
    ]
    for test in tests:
        print(f"\n--- {test.__name__} ---")
        test()

    print("\n" + "-" * 60)
    if _failures == 0:
        print("ALL CHAT ORCHESTRATOR INTEGRATION TESTS PASSED (real temp SQLite; fake embedder/vector-store/LLM provider — no real network)")
    else:
        print(f"{_failures} TEST(S) FAILED")


if __name__ == "__main__":
    run()
