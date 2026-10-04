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
"""

from dataclasses import dataclass, field
from typing import Callable, List, Optional

from app.conversation_context import build_conversation_context
from app.conversation_memory import add_message, create_session, session_exists
from app.escalation import EscalationResult, evaluate_escalation
from app.intent_router import IntentResult, classify_intent
from app.llm_provider import LLMProvider
from app.response_generator import generate_response
from app.retrieval import RetrievedArticle, retrieve_relevant_articles

GREETING_REPLY = "Hello! How can I help you today?"
ESCALATION_REPLY = (
    "I understand — let me connect you with a member of our support team "
    "who can help with this further."
)
EMPTY_MESSAGE_REPLY = "I didn't receive a message — could you try again?"


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


def _try_preprocess(message: str) -> bool:
    """
    Runs Day 3 preprocessing purely for its documented role as a
    pipeline stage (see module docstring, point 3). Returns whether it
    was actually available — never raises, since spaCy may not be
    installed (this sandbox) or the language model may be missing.
    """
    try:
        from app.preprocessing import preprocess

        preprocess(message)
        return True
    except Exception:
        return False


def handle_chat_message(
    message: str,
    session_id: Optional[str] = None,
    db_path: Optional[str] = None,
    provider: Optional[LLMProvider] = None,
    embed_fn: Optional[Callable[[List[str]], List[List[float]]]] = None,
    collection=None,
) -> ChatResult:
    """
    The real /chat pipeline. All external-boundary parameters
    (provider, embed_fn, collection) are optional dependency injection
    points — exactly the same pattern every Day 4-9 module already uses
    — defaulting to the real implementations. db_path threads through to
    every conversation_memory call for test isolation (Day 8's pattern).
    """
    if not session_id or not session_exists(session_id, db_path=db_path):
        session_id = create_session(db_path=db_path)

    message = (message or "").strip()
    if not message:
        return ChatResult(reply=EMPTY_MESSAGE_REPLY, session_id=session_id, message_id=None)

    preprocessing_available = _try_preprocess(message)

    # Fetch PRIOR history before storing the current message, so:
    #  (a) escalation's "repeated failure across turns" check doesn't
    #      double-count the message we're about to store, and
    #  (b) the LLM's conversation-history context is genuinely "what was
    #      said before now", not including the message it's answering.
    prior_history = build_conversation_context(session_id, db_path=db_path)
    prior_user_texts = [turn["content"] for turn in prior_history if turn.get("role") == "user"]

    add_message(session_id, "user", message, db_path=db_path)

    intent_result: IntentResult = classify_intent(message, embed_fn=embed_fn)

    escalation: EscalationResult = evaluate_escalation(
        message,
        intent_result=intent_result,
        recent_user_messages=prior_user_texts,
        embed_fn=embed_fn,
    )

    articles_used: List[str] = []
    used_llm = False

    if escalation.should_escalate:
        reply = ESCALATION_REPLY
    elif intent_result.intent == "greeting" and intent_result.method == "rule":
        reply = GREETING_REPLY
    else:
        retrieved: List[RetrievedArticle] = retrieve_relevant_articles(
            message, embed_fn=embed_fn, collection=collection
        )
        generated = generate_response(
            message, retrieved, provider=provider, conversation_history=prior_history
        )
        reply = generated.reply
        articles_used = generated.articles_used
        used_llm = not generated.used_fallback

    assistant_message_id = add_message(session_id, "assistant", reply, db_path=db_path)

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
    )
