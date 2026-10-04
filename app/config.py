"""
Central place for configuration constants, so values like model names and
folder paths aren't scattered across multiple files. Later days (LLM API
keys, SQLite path, escalation thresholds, etc.) will add to this file
rather than hard-coding values elsewhere.
"""

import os

# Load variables from a local .env file (if present) into the process
# environment — e.g. GEMINI_API_KEY. Safe to call even if .env doesn't
# exist (python-dotenv just does nothing in that case). This is the
# ONLY place the app reads a .env file, so secrets never get hard-coded
# elsewhere.
try:
    from dotenv import load_dotenv
    load_dotenv()
except ImportError:
    pass

# Project root = the folder that contains app/, knowledge_base/, etc.
BASE_DIR = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))

# Day 2: knowledge base source content
KNOWLEDGE_BASE_DIR = os.path.join(BASE_DIR, "knowledge_base")

# Day 4: embedding model specified in the official project brief
EMBEDDING_MODEL_NAME = "all-MiniLM-L6-v2"

# Day 4: ChromaDB persistent storage
# Persistent = the vector index is saved to disk, so we don't have to
# re-embed all 31 articles every time the app restarts.
CHROMA_PERSIST_DIR = os.path.join(BASE_DIR, "chroma_db")
CHROMA_COLLECTION_NAME = "smartassist_knowledge_base"

# Day 5: semantic retrieval settings
# DEFAULT_TOP_K: how many candidate articles to fetch per query by default.
DEFAULT_TOP_K = 3

# RELEVANCE_DISTANCE_THRESHOLD: ChromaDB returns a "distance" per result —
# smaller distance means more similar (it is NOT a 0-1 similarity score).
# A result is treated as relevant only if its distance is <= this value.
# NOTE: this starting value (1.0) is a reasonable default for cosine-style
# distance with all-MiniLM-L6-v2, but it has not been tuned against real
# queries yet (this sandbox can't run the real model — see README "Day 5"
# section). Adjust it after running scripts/evaluate_retrieval.py locally
# and looking at real distance numbers for good vs. bad matches.
RELEVANCE_DISTANCE_THRESHOLD = 1.0

# Day 6: LLM response generation
# Provider is chosen here, in one place, so swapping providers later
# does not require touching response_generator.py or main.py.
# We use Gemini as the primary provider because the project brief lists
# it as a free-tier option, which fits the "LLM API costs must stay
# within free tier limits" constraint.
LLM_PROVIDER = "gemini"  # "gemini" or "openai"

GEMINI_MODEL_NAME = "gemini-1.5-flash"
GEMINI_API_KEY_ENV_VAR = "GEMINI_API_KEY"

OPENAI_MODEL_NAME = "gpt-3.5-turbo"
OPENAI_API_KEY_ENV_VAR = "OPENAI_API_KEY"

# How long to wait for the LLM API before treating it as a timeout.
LLM_REQUEST_TIMEOUT_SECONDS = 10

# Context size limits, so we never blindly dump unlimited KB text into
# the prompt (keeps latency/cost down, per the brief's constraints).
MAX_CONTEXT_ARTICLES = 3
MAX_CHARS_PER_ARTICLE = 800

# Day 7: intent routing
# Confidence assigned to any rule-based match. Fixed (not "invented" per
# message) because a rule either matches or it doesn't — there's no
# partial-match score to report, so we use one deterministic constant
# and rely on the "matched_text" evidence field for explainability.
RULE_MATCH_CONFIDENCE = 0.95

# Minimum cosine similarity for the embedding-similarity fallback to
# accept an intent instead of returning "unknown". Not tuned against
# real embeddings yet (this sandbox can't run the real model) — adjust
# after testing locally with real queries.
SEMANTIC_CONFIDENCE_THRESHOLD = 0.55

# Cap how much of a message we process for intent routing, so an
# unusually long/malformed message can't cause excessive processing.
MAX_INTENT_INPUT_CHARS = 2000

# Day 8: SQLite conversation memory
CONVERSATION_DB_PATH = os.path.join(BASE_DIR, "conversations.db")

# How many of the most recent messages to retrieve as context for a
# session (the "sliding window"). This does NOT limit how many messages
# are stored — SQLite keeps the full history forever; this only limits
# how much of it gets pulled out for the AI to read at once, to keep
# prompts small, fast, and within free-tier API limits.
CONVERSATION_HISTORY_LIMIT = 10

VALID_MESSAGE_ROLES = {"user", "assistant"}

# Day 10: frontend static files (served by FastAPI, see app/main.py)
FRONTEND_DIR = os.path.join(BASE_DIR, "frontend")

# Day 11: admin panel
# Separate SQLite file from conversations.db — admin sessions/audit logs
# are a different concern from customer chat history and don't need to
# live in the same database.
ADMIN_DB_PATH = os.path.join(BASE_DIR, "admin.db")

# Admin credentials come from environment variables ONLY — never
# hard-coded, never committed. ADMIN_PASSWORD_HASH must be a SHA-256 hex
# digest (see scripts/generate_admin_password_hash.py), not a plaintext
# password — so even the .env file never contains the real password in
# readable form. If ADMIN_PASSWORD_HASH is unset, admin login always
# fails closed (safer default than an accidentally-open panel).
ADMIN_USERNAME_ENV_VAR = "ADMIN_USERNAME"
ADMIN_PASSWORD_HASH_ENV_VAR = "ADMIN_PASSWORD_HASH"
ADMIN_DEFAULT_USERNAME = "admin"  # used only if ADMIN_USERNAME env var is unset

ADMIN_SESSION_TTL_MINUTES = 120

# Knowledge base article validation limits (Day 11 add/edit)
MAX_ARTICLE_TITLE_LENGTH = 200
MAX_ARTICLE_CONTENT_CHARS = 20000
# Category and slug must be simple lowercase words/hyphens only — this
# is what makes path-traversal-by-category-name impossible (no "/", "..",
# or other path-control characters can ever match this pattern).
SAFE_NAME_PATTERN = r"^[a-z0-9][a-z0-9-]{0,79}$"

ADMIN_LOG_RETENTION_DEFAULT_LIMIT = 100

# Day 12: feedback system
VALID_FEEDBACK_RATINGS = {"helpful", "not_helpful"}
MAX_FEEDBACK_COMMENT_CHARS = 500
FEEDBACK_LOG_DEFAULT_LIMIT = 100

# Day 15: how many prior conversation turns (role/content pairs) to
# include in the LLM prompt for context. Capped independently of
# CONVERSATION_HISTORY_LIMIT (which controls /history's sliding window)
# to keep the prompt itself small regardless of how much history exists.
MAX_HISTORY_MESSAGES_IN_PROMPT = 6

# Day 9: escalation / frustration detection
# If the intent router's confidence is below this, we escalate to a
# human rather than let a low-confidence AI guess handle the customer.
# Applies regardless of which intent was returned (including "unknown").
INTENT_ESCALATION_THRESHOLD = 0.6

# Frustration score (0.0-1.0) is the fraction of distinct frustration
# signal categories matched in a message (see app/escalation.py). This
# threshold is deliberately > "one category" (0.2 for 5 categories) so a
# single keyword never triggers frustration on its own — see the brief's
# requirement to reduce false positives.
FRUSTRATION_SCORE_THRESHOLD = 0.4

# How many of the most recent user turns (from Day 8 conversation
# history) to check for repeated failure/frustration language when
# deciding if frustration is building up across a conversation, not
# just in the current message.
FRUSTRATION_HISTORY_WINDOW = 5
