"""
Regression tests for the production crash

    File "app/chat_orchestrator.py", line 250, in handle_chat_message
        fallback_reason = generated.fallback_reason
    AttributeError: 'GeneratedResponse' object has no attribute 'fallback_reason'

These tests use the REAL generate_response() (only the LLM provider, embedder and
vector collection are fakes), so any drift between GeneratedResponse and
handle_chat_message fails here. Plain `assert`s: they really fail under pytest
and under `python -m tests.test_fallback_reason_regression`.

Run:
    python -m tests.test_fallback_reason_regression
    python -m pytest tests/test_fallback_reason_regression.py     (if pytest is installed)
"""

import dataclasses
import os
import shutil
import tempfile

from app.chat_orchestrator import ChatResult, handle_chat_message
from app.conversation_memory import init_db
from app.llm_provider import LLMAPIError, LLMProvider, LLMRateLimitError, LLMTimeoutError, MissingAPIKeyError
from app.response_generator import GeneratedResponse, generate_response
from scripts.check_response_contract import attributes_read


def fake_embed(texts):
    return [[0.1, 0.2, 0.3] for _ in texts]


class OkProvider(LLMProvider):
    def generate(self, system_prompt, user_prompt):
        return "Go to Settings and click Forgot Password."


class RaisingProvider(LLMProvider):
    def __init__(self, exc):
        self.exc = exc

    def generate(self, system_prompt, user_prompt):
        raise self.exc


class BlankProvider(LLMProvider):
    def generate(self, system_prompt, user_prompt):
        return "   "


class ArticleCollection:
    def query(self, query_embeddings, n_results, include):
        return {
            "ids": [["account-001"]],
            "documents": [["To reset your password, go to Settings and click Forgot Password."]],
            "metadatas": [[{"title": "Reset password", "category": "account", "source_filename": "f.md", "source_path": "/kb/f.md"}]],
            "distances": [[0.2]],
        }


class EmptyCollection:
    def query(self, query_embeddings, n_results, include):
        return {"ids": [[]], "documents": [[]], "metadatas": [[]], "distances": [[]]}


def _run(provider, collection, message="how do I reset my password"):
    tmp = tempfile.mkdtemp()
    try:
        db_path = os.path.join(tmp, "t.db")
        init_db(db_path=db_path)
        return handle_chat_message(message, db_path=db_path, provider=provider,
                                   embed_fn=fake_embed, collection=collection)
    finally:
        shutil.rmtree(tmp, ignore_errors=True)


def test_every_attribute_orchestrator_reads_exists_on_generated_response():
    fields = {f.name for f in dataclasses.fields(GeneratedResponse)}
    read = attributes_read("app/chat_orchestrator.py", "generated")
    assert read, "expected chat_orchestrator to read attributes from `generated`"
    assert read <= fields, f"orchestrator reads {sorted(read - fields)} which GeneratedResponse lacks"
    assert "fallback_reason" in fields


def test_chat_result_carries_every_field_main_reads():
    fields = {f.name for f in dataclasses.fields(ChatResult)}
    assert attributes_read("app/main.py", "result") <= fields


def test_normal_generated_response_does_not_crash():
    r = _run(OkProvider(), ArticleCollection())
    assert r.reply == "Go to Settings and click Forgot Password."
    assert r.used_llm is True
    assert r.fallback_reason is None
    assert r.articles_used == ["account-001"]
    assert r.message_id is not None


def test_fallback_with_relevant_article_does_not_crash():
    r = _run(RaisingProvider(LLMAPIError("boom")), ArticleCollection())
    assert r.used_llm is False
    assert r.fallback_reason == "llm_error"
    assert "Reset password" in r.reply
    assert r.articles_used == ["account-001"]
    assert r.escalated is False
    assert r.message_id is not None


def test_fallback_without_article_does_not_crash():
    r = _run(RaisingProvider(LLMTimeoutError("slow")), EmptyCollection())
    assert r.used_llm is False
    assert r.fallback_reason == "llm_timeout"
    assert r.reply.strip()
    assert r.articles_used == []


def test_every_fallback_reason_survives_the_full_pipeline():
    cases = [
        (RaisingProvider(MissingAPIKeyError("no key")), "missing_api_key"),
        (RaisingProvider(LLMTimeoutError("t")), "llm_timeout"),
        (RaisingProvider(LLMRateLimitError("r")), "llm_rate_limited"),
        (RaisingProvider(LLMAPIError("e")), "llm_error"),
        (RaisingProvider(KeyError("unexpected sdk error")), "llm_error"),
        (BlankProvider(), "llm_empty"),
    ]
    for provider, expected in cases:
        for collection in (ArticleCollection(), EmptyCollection()):
            r = _run(provider, collection)
            assert r.used_llm is False and r.fallback_reason == expected, (expected, r)
            assert r.reply.strip()


def test_generate_response_sets_fallback_reason_consistently():
    ok = generate_response("hello there question", [], provider=OkProvider())
    assert ok.used_fallback is False and ok.fallback_reason is None
    bad = generate_response("hello there question", [], provider=RaisingProvider(LLMAPIError("x")))
    assert bad.used_fallback is True and bad.fallback_reason == "llm_error"
    empty = generate_response("   ", [], provider=OkProvider())
    assert empty.used_fallback is True and empty.fallback_reason == "empty_query"


def test_other_pipeline_branches_still_work():
    tmp = tempfile.mkdtemp()
    try:
        db_path = os.path.join(tmp, "t.db")
        init_db(db_path=db_path)
        greet = handle_chat_message("hi", db_path=db_path, provider=OkProvider(), embed_fn=fake_embed, collection=EmptyCollection())
        assert greet.reply == "Hello! How can I help you today?" and greet.fallback_reason is None
        esc = handle_chat_message("I want to talk to a human agent", greet.session_id, db_path=db_path,
                                  provider=OkProvider(), embed_fn=fake_embed, collection=EmptyCollection())
        assert esc.escalated is True and esc.handoff_confirmed is False and esc.fallback_reason is None
    finally:
        shutil.rmtree(tmp, ignore_errors=True)


if __name__ == "__main__":
    tests = [(n, f) for n, f in sorted(globals().items()) if n.startswith("test_") and callable(f)]
    failures = 0
    for name, fn in tests:
        try:
            fn()
            print(f"[PASS] {name}")
        except Exception as exc:  # noqa: BLE001
            failures += 1
            print(f"[FAIL] {name}: {type(exc).__name__}: {exc}")
    print("-" * 60)
    print(f"{len(tests) - failures}/{len(tests)} passed")
    raise SystemExit(1 if failures else 0)
