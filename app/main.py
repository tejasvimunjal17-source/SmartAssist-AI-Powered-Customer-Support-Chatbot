import logging
import threading
import time
from typing import Dict, List, Optional

from fastapi import FastAPI, HTTPException, Request, Response
from fastapi.staticfiles import StaticFiles
from pydantic import BaseModel

from app.admin_routes import router as admin_router
from app.admin_store import init_admin_db
from app.chat_orchestrator import handle_chat_message
from app.config import FRONTEND_DIR, WARMUP_ON_STARTUP
from app.conversation_memory import get_recent_history, init_db
from app.feedback import FeedbackValidationError, submit_feedback
from app.inflight import release as release_inflight
from app.inflight import try_acquire as acquire_inflight

# uvicorn only configures its own loggers; make sure our structured timing
# logs ("smartassist.perf") actually reach the Railway log stream.
_perf_logger = logging.getLogger("smartassist.perf")
if not _perf_logger.handlers:
    _handler = logging.StreamHandler()
    _handler.setFormatter(logging.Formatter("%(message)s"))
    _perf_logger.addHandler(_handler)
    _perf_logger.setLevel(logging.INFO)
    _perf_logger.propagate = False

app = FastAPI(title="SmartAssist", description="AI-Powered Customer Support Chatbot")
app.include_router(admin_router)


@app.middleware("http")
async def add_process_time_header(request: Request, call_next):
    """Total backend time for every request, as a response header (monotonic clock)."""
    started = time.perf_counter()
    response = await call_next(request)
    response.headers["X-Process-Time-Ms"] = f"{(time.perf_counter() - started) * 1000.0:.1f}"
    return response


def _warm_up_resources():
    """
    Runs in a background thread so the port opens immediately (Railway
    health check passes) while the heavy resources load. Loads the
    embedding model, builds the vector index if the container has none,
    opens the vector store, and creates the Gemini client - so the first
    customer message doesn't pay for any of it. Never raises.
    """
    log = logging.getLogger("smartassist.perf")
    t0 = time.perf_counter()
    try:
        from app.embeddings import warm_up
        from app.vector_store import ensure_index_built, get_collection

        embed_ok = warm_up()
        indexed = ensure_index_built() if embed_ok else 0
        try:
            get_collection()
        except Exception:
            pass
        try:
            from app.llm_provider import get_default_provider

            provider = get_default_provider()
            try:
                provider._get_client()
            except Exception:
                pass  # missing key etc. is handled per-request
        except Exception:
            pass
        log.info('{"event": "warmup_done", "embedder_ready": %s, "articles_indexed": %d, "ms": %.0f}',
                 str(embed_ok).lower(), indexed, (time.perf_counter() - t0) * 1000.0)
    except Exception:
        log.info('{"event": "warmup_failed"}')


@app.on_event("startup")
def on_startup():
    # Creates conversations.db + tables if they don't exist yet. Safe to
    # run every time the app starts (CREATE TABLE IF NOT EXISTS). Day 12's
    # feedback table is created here too, inside init_db().
    init_db()
    # Day 11: creates admin.db (sessions + audit log tables), same way.
    init_admin_db()
    if WARMUP_ON_STARTUP:
        threading.Thread(target=_warm_up_resources, name="warmup", daemon=True).start()


class ChatRequest(BaseModel):
    message: str
    session_id: Optional[str] = None  # Day 8: omit to start a new session


class ChatResponse(BaseModel):
    reply: str
    session_id: str
    # Day 12 addition: the assistant message's row id, so the frontend
    # can submit feedback against this specific response. Optional/None
    # for the empty-message case below, where nothing was actually
    # stored. Purely additive — existing clients reading reply/session_id
    # are unaffected.
    message_id: Optional[int] = None
    # Day 15 additions: informational metadata from the now-integrated
    # pipeline. All optional/default-None — a client that only reads
    # reply/session_id/message_id sees no difference at all.
    intent: Optional[str] = None
    intent_confidence: Optional[float] = None
    escalated: bool = False
    escalation_reason: Optional[str] = None
    articles_used: List[str] = []
    used_llm: bool = False
    # Additive fields (clients that ignore them are unaffected):
    # handoff_confirmed is always False - there is no live-agent integration.
    handoff_confirmed: bool = False
    fallback_reason: Optional[str] = None  # why the LLM wasn't used, if it wasn't
    timings_ms: Optional[Dict[str, float]] = None


class HistoryMessage(BaseModel):
    id: int  # Day 12 addition: lets reloaded history also support feedback
    role: str
    content: str
    timestamp: str


class HistoryResponse(BaseModel):
    session_id: str
    history: List[HistoryMessage]


class FeedbackRequest(BaseModel):
    session_id: str
    message_id: int
    rating: str  # "helpful" or "not_helpful"
    comment: Optional[str] = None


class FeedbackResponse(BaseModel):
    status: str = "success"
    feedback_id: int
    updated: bool  # True if this changed a previous rating rather than creating a new one


@app.get("/")
def read_root():
    return {"status": "SmartAssist is running"}


@app.post("/chat", response_model=ChatResponse)
def chat(request: ChatRequest, response: Response):
    """
    Full pipeline: intent routing, escalation check, RAG retrieval,
    conversation history and one Gemini call (see app.chat_orchestrator).

    Only one request per session may be in flight: a second concurrent
    request for the same session_id gets HTTP 429 instead of storing the
    message twice and paying for a duplicate LLM call.

    The Server-Timing response header reports local processing vs Gemini
    wait vs total, so end-to-end latency can be compared in browser devtools.
    """
    session_id = request.session_id
    guarded = bool(session_id and session_id.strip())
    if guarded and not acquire_inflight(session_id):
        raise HTTPException(status_code=429, detail="A message is already being processed for this conversation.")

    try:
        result = handle_chat_message(request.message, session_id=session_id)
    finally:
        if guarded:
            release_inflight(session_id)

    t = result.timings_ms or {}
    response.headers["Server-Timing"] = (
        f"total;dur={t.get('total', 0)}, local;dur={t.get('local', 0)}, llm;dur={t.get('llm_wait', 0)}"
    )

    return ChatResponse(
        reply=result.reply,
        session_id=result.session_id,
        message_id=result.message_id,
        intent=result.intent,
        intent_confidence=result.intent_confidence,
        escalated=result.escalated,
        escalation_reason=result.escalation_reason,
        articles_used=result.articles_used,
        used_llm=result.used_llm,
        handoff_confirmed=result.handoff_confirmed,
        fallback_reason=result.fallback_reason,
        timings_ms=result.timings_ms,
    )


@app.get("/history", response_model=HistoryResponse)
def history(session_id: str, limit: Optional[int] = None):
    """
    Returns the recent conversation history for a session (sliding
    window — see app.config.CONVERSATION_HISTORY_LIMIT). An unknown
    session returns an empty history rather than an error, since a
    session simply not existing (e.g. expired, mistyped, never started)
    is a normal, safe case for a chat client to encounter.
    """
    if not session_id or not session_id.strip():
        raise HTTPException(status_code=400, detail="session_id is required")

    kwargs = {"limit": limit} if limit is not None else {}
    messages = get_recent_history(session_id, **kwargs)

    return HistoryResponse(
        session_id=session_id,
        history=[HistoryMessage(**m) for m in messages],
    )


@app.post("/feedback", response_model=FeedbackResponse)
def feedback(request: FeedbackRequest):
    """
    Day 12: stores helpful/not-helpful feedback on a specific assistant
    message. The message_id/session_id pair is verified server-side
    against real stored data (see app.feedback.submit_feedback) — a
    request can't attach feedback to a message it doesn't actually own,
    a nonexistent message, or a non-assistant message, no matter what
    the client claims.
    """
    try:
        result = submit_feedback(
            session_id=request.session_id,
            message_id=request.message_id,
            rating=request.rating,
            comment=request.comment,
        )
    except FeedbackValidationError as exc:
        raise HTTPException(status_code=400, detail=str(exc))

    return FeedbackResponse(feedback_id=result["id"], updated=result["updated"])


# Day 10: serve the web chat interface.
#
# Mounted at "/app" (not "/") so the existing "/" JSON status endpoint
# above is preserved exactly as-is, per the Day 10 requirement to not
# break any existing endpoint. html=True makes StaticFiles automatically
# serve frontend/index.html for "/app/" and "/app/", and also serves
# frontend/style.css and frontend/app.js at "/app/style.css" and
# "/app/app.js" (which is what index.html's <link>/<script> tags
# reference — no separate "/static" mount needed).
app.mount("/app", StaticFiles(directory=FRONTEND_DIR, html=True), name="frontend")
