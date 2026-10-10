"""
Day 15: wires the previously-separate Day 3/4/5/6/7/8/9 modules into the
actual customer-facing pipeline the project brief describes:

    User Input
        -> Preprocessing        (Day 3, informational only — see note below)
        -> Intent Classifier    (Day 7)
        -> Escalation check     (Day 9 — explicit human request wins immediately)
        -> RAG Retriever        (Day 5, skipped if escalating or a plain greeting)
        -> Conversation Context (Day 8, prior turns feed the LLM prompt)
        -> LLM Generator        (Day 6)
        -> Response
        -> Conversation persistence (Day 8)

Kept OUT of app/main.py on purpose — same pattern as app/admin_service.py
sitting behind app/admin_routes.py — so this full pipeline is testable
without FastAPI installed, and main.py's route stays a thin wrapper.

DESIGN DECISIONS WORTH DOCUMENTING (none of these change any existing
module's behavior — they're choices about how this orchestrator calls
them):

1. Escalation is checked BEFORE retrieval/LLM, using the message's
   ALREADY-classified intent and the PRIOR conversation's user messages
   (fetched before the current message is stored, so "repeated failure
   across turns" doesn't double-count the current message). If
   escalation triggers, a fixed, honest acknowledgment is returned and
   the RAG/LLM steps are skipped entirely.
2. A confidently rule-matched "greeting" also skips RAG/LLM, with a
   short scripted reply. Without this, a plain "hi" would retrieve no
   relevant article and get the (correct, but unfriendly) "I don't have
   information about that" fallback. This is a minimal, low-risk use of
   intent classification's output, not a new claim about the system's
   capabilities.
3. Preprocessing (Day 3, spaCy) is invoked for its own sake, to honor
   the documented pipeline stage, but its cleaned/lemmatized/stopword-
   stripped output is NOT fed into intent routing or retrieval. Both of
   those modules already do their own internal text normalization suited
   to their exact needs (e.g. intent_router's rule phrases like "how do
   i" rely on stopwords like "do" and "i" that preprocessing would
   strip) — feeding them stopword-stripped text would break their
   already-tested matching, not improve it. Preprocessing's result is
   only attached to the orchestrator's result as informational metadata
   (and is simply absent if spaCy isn't installed — see
   preprocessing_available below).
4. Every external dependency (spaCy, the embedding model, ChromaDB, the
   Gemini API) is wrapped so its absence or failure degrades gracefully
   instead of crashing the request — reusing each module's OWN existing
   defensive behavior rather than re-implementing it here.

ENVIRONMENT NOTE: this sandbox has no internet access, so it has none of
sentence-transformers/ChromaDB/spaCy/a real Gemini key available. Every
test exercising this orchestrator uses fakes/mocks for those boundaries
(see tests/test_chat_orchestrator.py) and the orchestrator's own real,
honest degradation for the rest — nothing here claims a real embedding,
retrieval, or LLM call succeeded unless it actually did.

PERFORMANCE / CORRECTNESS REVISION (this version)
-------------------------------------------------
* Low intent-confidence no longer escalates by itself (root cause of the
  "connect you with a member of our support team" loop - see config.py).
* The RAG/LLM stage now runs for every non-greeting, non-escalated
  message, including ones the knowledge base doesn't match.
* A message is only answered with the scripted greeting if it is
  *purely* a greeting. "Hello, how do I reset my password?" is a real
  question and goes to RAG+LLM (before, the word "hello" alone matched
  the greeting rule and swallowed the question).
* spaCy preprocessing is no longer run per request (its output was
  discarded). Opt back in with RUN_SPACY_PREPROCESSING=true.
* One embedding is computed per request and shared between intent
  classification and retrieval (per-request memo, discarded afterwards -
  nothing about a conversation is cached across requests).
* Every stage is timed (monotonic clock); see app/timing.py.
* The escalation reply never claims a human was contacted: there is no
  handoff integration, so handoff_confirmed is always False.
"""

import re
from dataclasses import dataclass, field
from typing import Callable, Dict, List, Optional

from app.config import RUN_SPACY_PREPROCESSING
from app.conversation_context import build_conversation_context
from app.conversation_memory import add_message, create_session, session_exists
from app.embeddings import embed_texts
from app.escalation import EscalationResult, evaluate_escalation
from app.intent_router import IntentResult, classify_intent
from app.llm_provider import LLMProvider
from app.response_generator import generate_response
from app.retrieval import RetrievedArticle, retrieve_relevant_articles
from app.timing import StageTimer

GREETING_REPLY = "Hello! How can I help you today?"

# HONEST handoff wording: this app has no live-agent integration, so it must
# not say it has connected/contacted anyone.
ESCALATION_REPLY = (
    "I can't transfer you to a person from this chat, and I haven't notified "
    "anyone yet. To reach a human agent, please contact our support team "
    "directly using the contact details on our website or in your account. "
    "If you tell me what's going on, I'll do my best to help in the meantime."
)
FRUSTRATION_REPLY = (
    "I'm sorry this has been frustrating. I can't transfer you to a person from "
    "this chat, and I haven't notified anyone yet - to reach a human agent, please "
    "contact our support team directly using the contact details on our website or "
    "in your account. If you share the details, I'll try to help right here."
)
EMPTY_MESSAGE_REPLY = "I didn't receive a message — could you try again?"

_GREETING_WORDS = {
    "hi", "hello", "hey", "yo", "hiya", "howdy", "greetings", "there", "good",
    "morning", "afternoon", "evening", "how", "are", "you", "doing", "whats",
    "what's", "up", "sup", "all", "is", "it", "going", "today", "friend",
}
_WORD_RE = re.compile(r"[a-z']+")


def _is_pure_greeting(message: str) -> bool:
    """True only when the message is nothing but a greeting ("hi", "hello there",
    "good morning, how are you?"). Any other content makes it a real question."""
    words = _WORD_RE.findall(message.lower())
    if not words or len(words) > 6:
        return False
    return words[0] in {"hi", "hello", "hey", "yo", "hiya", "howdy", "greetings", "good", "sup", "whats", "what's"} and all(
        w in _GREETING_WORDS for w in words
    )


@dataclass
class ChatResult:
    reply: str
    session_id: str
    message_id: Optional[int]
    intent: Optional[str] = None
    intent_confidence: Optional[float] = None
    escalated: bool = False
    escalation_reason: Optional[str] = None
    articles_used: List[str] = field(default_factory=list)
    used_llm: bool = False
    preprocessing_available: bool = False
    # Always False: there is no live-agent integration to confirm a handoff.
    handoff_confirmed: bool = False
    # Why the LLM wasn't used (None if it was, or if no LLM call was needed).
    fallback_reason: Optional[str] = None
    # Stage timings in ms (stage names only, no content).
    timings_ms: Dict[str, float] = field(default_factory=dict)


def _try_preprocess(message: str) -> bool:
    """Optional spaCy stage (off by default - output was never used)."""
    if not RUN_SPACY_PREPROCESSING:
        return False
    try:
        from app.preprocessing import preprocess

        preprocess(message)
        return True
    except Exception:
        return False


def _request_embedder(base_fn: Callable[[List[str]], List[List[float]]]):
    """
    Per-request memo around the embedder: intent classification and retrieval
    both embed the user's message - compute it once. The memo lives only for
    this request (closure), so nothing about a conversation is retained.
    """
    memo: Dict[str, List[float]] = {}

    def embed(texts: List[str]) -> List[List[float]]:
        if len(texts) == 1:
            key = texts[0].strip().lower()
            if key not in memo:
                memo[key] = base_fn(texts)[0]
            return [memo[key]]
        return base_fn(texts)

    embed._cacheable = base_fn is embed_texts  # lets intent_router cache example vectors
    return embed


def handle_chat_message(
    message: str,
    session_id: Optional[str] = None,
    db_path: Optional[str] = None,
    provider: Optional[LLMProvider] = None,
    embed_fn: Optional[Callable[[List[str]], List[List[float]]]] = None,
    collection=None,
) -> ChatResult:
    """
    The real /chat pipeline. External boundaries (provider, embed_fn,
    collection) are injectable for tests; db_path isolates SQLite in tests.
    """
    timer = StageTimer()

    with timer.stage("preprocess"):
        if not session_id or not session_exists(session_id, db_path=db_path):
            session_id = create_session(db_path=db_path)

        message = (message or "").strip()
        if not message:
            return ChatResult(reply=EMPTY_MESSAGE_REPLY, session_id=session_id, message_id=None,
                              timings_ms=timer.summary())

        preprocessing_available = _try_preprocess(message)

    with timer.stage("history"):
        # PRIOR history, fetched before storing the current message.
        prior_history = build_conversation_context(session_id, db_path=db_path)
        prior_user_texts = [t["content"] for t in prior_history if t.get("role") == "user"]
        add_message(session_id, "user", message, db_path=db_path)

    embed = _request_embedder(embed_fn or embed_texts)

    with timer.stage("intent"):
        intent_result: IntentResult = classify_intent(message, embed_fn=embed)

    with timer.stage("escalation"):
        escalation: EscalationResult = evaluate_escalation(
            message,
            intent_result=intent_result,
            recent_user_messages=prior_user_texts,
            embed_fn=embed,
        )

    articles_used: List[str] = []
    used_llm = False
    fallback_reason: Optional[str] = None

    if escalation.should_escalate:
        explicit = escalation.reason == "explicit_human_request"
        reply = ESCALATION_REPLY if explicit else FRUSTRATION_REPLY
    elif _is_pure_greeting(message):
        reply = GREETING_REPLY
    else:
        with timer.stage("retrieval"):
            retrieved: List[RetrievedArticle] = retrieve_relevant_articles(
                message, embed_fn=embed, collection=collection
            )
        # generate_response makes the single Gemini call; time it separately
        # so local processing and LLM waiting can be told apart.
        with timer.stage("llm"):
            generated = generate_response(
                message, retrieved, provider=provider, conversation_history=prior_history
            )
        reply = generated.reply
        articles_used = generated.articles_used
        used_llm = not generated.used_fallback
        fallback_reason = generated.fallback_reason

    with timer.stage("persist"):
        assistant_message_id = add_message(session_id, "assistant", reply, db_path=db_path)

    timer.log({
        "intent": intent_result.intent,
        "intent_method": intent_result.method,
        "escalated": escalation.should_escalate,
        "used_llm": used_llm,
        "fallback_reason": fallback_reason,
        "articles": len(articles_used),
    })

    return ChatResult(
        reply=reply,
        session_id=session_id,
        message_id=assistant_message_id,
        intent=intent_result.intent,
        intent_confidence=intent_result.confidence,
        escalated=escalation.should_escalate,
        escalation_reason=escalation.reason if escalation.should_escalate else None,
        articles_used=articles_used,
        used_llm=used_llm,
        preprocessing_available=preprocessing_available,
        handoff_confirmed=False,
        fallback_reason=fallback_reason,
        timings_ms=timer.summary(),
    )
