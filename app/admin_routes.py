"""
Day 11: admin API routes, kept in their own APIRouter (mounted into
app/main.py) so the public customer-facing routes in main.py stay
untouched and easy to read separately from admin concerns.

Every route except /admin/login depends on require_admin — FastAPI runs
that dependency before the route body executes, so an unauthorized
request never reaches any admin logic at all, regardless of what URL it
guesses.
"""

from typing import List, Optional

from fastapi import APIRouter, Depends, HTTPException
from pydantic import BaseModel

from app.admin_auth import issue_session_token, verify_admin_credentials
from app.admin_dependencies import require_admin
from app.admin_service import add_article_and_reindex, edit_article_and_reindex, refresh_index
from app.admin_store import get_recent_logs, log_admin_action
from app.config import ADMIN_LOG_RETENTION_DEFAULT_LIMIT, FEEDBACK_LOG_DEFAULT_LIMIT
from app.feedback import get_recent_feedback
from app.kb_admin import ArticleValidationError, get_article, list_articles

router = APIRouter(prefix="/admin")


# --- Schemas ---------------------------------------------------------------

class LoginRequest(BaseModel):
    username: str
    password: str


class LoginResponse(BaseModel):
    token: str


class ArticleSummary(BaseModel):
    id: str
    title: str
    category: str
    source_filename: str


class ArticleDetail(ArticleSummary):
    content: str


class CreateArticleRequest(BaseModel):
    title: str
    category: str
    content: str


class EditArticleRequest(BaseModel):
    content: str


class IndexRefreshResponse(BaseModel):
    attempted: bool
    success: bool
    articles_indexed: int
    error: Optional[str] = None


class LogEntry(BaseModel):
    action: str
    detail: str
    status: str
    created_at: str


class FeedbackEntry(BaseModel):
    id: int
    session_id: str
    message_id: int
    rating: str
    comment: Optional[str] = None
    created_at: str


# --- Auth --------------------------------------------------------------

@router.post("/login", response_model=LoginResponse)
def admin_login(request: LoginRequest):
    """
    Public endpoint (no require_admin — this IS the auth check). Fails
    closed: if ADMIN_PASSWORD_HASH isn't configured, every login attempt
    fails, never succeeds "by accident".
    """
    if not verify_admin_credentials(request.username, request.password):
        log_admin_action("admin_login", detail=request.username, status="failed")
        raise HTTPException(status_code=401, detail="Invalid admin credentials")

    token = issue_session_token()
    log_admin_action("admin_login", detail=request.username, status="success")
    return LoginResponse(token=token)


# --- Knowledge base management ------------------------------------------

@router.get("/knowledge", response_model=List[ArticleSummary], dependencies=[Depends(require_admin)])
def admin_list_knowledge():
    articles = list_articles()
    return [
        ArticleSummary(id=a.id, title=a.title, category=a.category, source_filename=a.source_filename)
        for a in articles
    ]


@router.get("/knowledge/{article_id}", response_model=ArticleDetail, dependencies=[Depends(require_admin)])
def admin_get_knowledge(article_id: str):
    article = get_article(article_id)
    if article is None:
        raise HTTPException(status_code=404, detail="Article not found")
    return ArticleDetail(
        id=article.id,
        title=article.title,
        category=article.category,
        source_filename=article.source_filename,
        content=article.content,
    )


@router.post("/knowledge", response_model=ArticleDetail, dependencies=[Depends(require_admin)])
def admin_create_knowledge(request: CreateArticleRequest):
    try:
        article, _refresh = add_article_and_reindex(request.title, request.category, request.content)
    except ArticleValidationError as exc:
        raise HTTPException(status_code=400, detail=str(exc))

    return ArticleDetail(
        id=article.id,
        title=article.title,
        category=article.category,
        source_filename=article.source_filename,
        content=article.content,
    )


@router.put("/knowledge/{article_id}", response_model=ArticleDetail, dependencies=[Depends(require_admin)])
def admin_edit_knowledge(article_id: str, request: EditArticleRequest):
    try:
        article, _refresh = edit_article_and_reindex(article_id, request.content)
    except ArticleValidationError as exc:
        raise HTTPException(status_code=400, detail=str(exc))

    return ArticleDetail(
        id=article.id,
        title=article.title,
        category=article.category,
        source_filename=article.source_filename,
        content=article.content,
    )


@router.post("/knowledge/refresh-index", response_model=IndexRefreshResponse, dependencies=[Depends(require_admin)])
def admin_refresh_index():
    result = refresh_index()
    return IndexRefreshResponse(
        attempted=result.attempted,
        success=result.success,
        articles_indexed=result.articles_indexed,
        error=result.error,
    )


# --- Logs ----------------------------------------------------------------

@router.get("/logs", response_model=List[LogEntry], dependencies=[Depends(require_admin)])
def admin_get_logs(limit: int = ADMIN_LOG_RETENTION_DEFAULT_LIMIT):
    logs = get_recent_logs(limit=limit)
    return [LogEntry(**entry) for entry in logs]


# --- Feedback (Day 12) -----------------------------------------------------
# Raw listing only, for visibility — aggregated metrics/analytics are
# Day 13's scope, not implemented here.

@router.get("/feedback", response_model=List[FeedbackEntry], dependencies=[Depends(require_admin)])
def admin_get_feedback(limit: int = FEEDBACK_LOG_DEFAULT_LIMIT):
    entries = get_recent_feedback(limit=limit)
    return [FeedbackEntry(**entry) for entry in entries]
