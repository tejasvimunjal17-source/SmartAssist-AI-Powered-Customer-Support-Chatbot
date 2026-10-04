"""
Day 11: the FastAPI-specific half of admin authentication — kept
separate from app/admin_auth.py so that file's pure credential-checking
logic can be imported and tested without FastAPI installed.

require_admin() is a FastAPI dependency: add `Depends(require_admin)` to
any admin route. Expects header: Authorization: Bearer <token>
Raises 401 if missing, malformed, unknown, or expired. A normal customer
session (Day 8) is a completely different, unrelated concept and can
never satisfy this check.
"""

from typing import Optional

from fastapi import Header, HTTPException

from app.admin_store import validate_admin_session


def require_admin(authorization: Optional[str] = Header(None)) -> None:
    if not authorization or not authorization.startswith("Bearer "):
        raise HTTPException(status_code=401, detail="Missing or malformed admin authorization")

    token = authorization[len("Bearer "):].strip()
    if not validate_admin_session(token):
        raise HTTPException(status_code=401, detail="Invalid or expired admin session")
