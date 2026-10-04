"""
Day 11: admin credential verification (pure logic, no web framework
dependency — see app/admin_dependencies.py for the FastAPI-specific
require_admin() dependency that uses these functions).

Kept framework-free on purpose: this is exactly what lets
verify_admin_credentials() be tested directly in this sandbox, where
FastAPI itself isn't installed (no internet access here — see earlier
days' README notes). The same separation pattern is used throughout
this project (e.g. app/retrieval.py never imports chromadb directly).

Design:
- Credentials come from environment variables only (ADMIN_USERNAME,
  ADMIN_PASSWORD_HASH), never hard-coded, never committed.
- ADMIN_PASSWORD_HASH is a SHA-256 hex digest, not a plaintext password
  (see scripts/generate_admin_password_hash.py) — so even a leaked .env
  file doesn't directly reveal the real password.
- On successful login, a random opaque session token is issued and
  stored server-side (app.admin_store) with an expiry. The token itself
  carries no meaning — it's just a lookup key — so nothing can be
  forged client-side.

HONESTY NOTE: this is a lightweight authentication approach suitable for
an internship project demo, not an enterprise-grade auth system (no
password complexity rules, no rate limiting, no refresh tokens, no
multi-admin support). That's explicitly acceptable per the Day 11
brief ("do not claim enterprise-grade security").
"""

import hashlib
import hmac
import os
from typing import Optional

from app.admin_store import create_admin_session
from app.config import (
    ADMIN_DEFAULT_USERNAME,
    ADMIN_PASSWORD_HASH_ENV_VAR,
    ADMIN_USERNAME_ENV_VAR,
)


def _get_configured_username() -> str:
    return os.environ.get(ADMIN_USERNAME_ENV_VAR, ADMIN_DEFAULT_USERNAME)


def _get_configured_password_hash() -> Optional[str]:
    return os.environ.get(ADMIN_PASSWORD_HASH_ENV_VAR)


def verify_admin_credentials(username: str, password: str) -> bool:
    """
    Returns True only if username matches the configured admin username
    AND sha256(password) matches the configured hash. If no hash is
    configured at all, login always fails (fail closed, not fail open).

    Uses hmac.compare_digest for the hash comparison to avoid leaking
    timing information about how much of the hash matched.
    """
    configured_hash = _get_configured_password_hash()
    if not configured_hash:
        return False

    if username != _get_configured_username():
        return False

    submitted_hash = hashlib.sha256((password or "").encode("utf-8")).hexdigest()
    return hmac.compare_digest(submitted_hash, configured_hash)


def issue_session_token() -> str:
    return create_admin_session()
