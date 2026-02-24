"""FastAPI dependency injection for the dashboard.

Provides reusable dependencies for authentication and data access
that can be injected into route handlers via ``Depends()``.
"""

from __future__ import annotations

from typing import Annotated

from fastapi import Depends, HTTPException, Request
from starlette.status import HTTP_303_SEE_OTHER

from src.dashboard.auth import SESSION_COOKIE_NAME, session_manager
from src.database.repository import Repository


# ────────────────────────────────────────────────────────────
#  Repository dependency
# ────────────────────────────────────────────────────────────


async def get_repository() -> Repository:
    """Provide a ``Repository`` instance to route handlers."""
    return Repository()


# ────────────────────────────────────────────────────────────
#  Authentication dependency
# ────────────────────────────────────────────────────────────


async def get_current_user(request: Request) -> dict:
    """Extract and verify the session cookie.

    Returns:
        ``{"username": "admin"}`` if the session is valid.

    Raises:
        HTTPException(303): Redirect to ``/login`` when the session
        is missing, invalid, or expired.
    """
    token = request.cookies.get(SESSION_COOKIE_NAME)
    if not token:
        raise HTTPException(
            status_code=HTTP_303_SEE_OTHER,
            headers={"Location": "/login"},
            detail="Not authenticated",
        )

    session_data = session_manager.verify_session(token)
    if session_data is None:
        raise HTTPException(
            status_code=HTTP_303_SEE_OTHER,
            headers={"Location": "/login"},
            detail="Session expired",
        )

    return session_data


# ────────────────────────────────────────────────────────────
#  Type aliases for cleaner route signatures
# ────────────────────────────────────────────────────────────

CurrentUser = Annotated[dict, Depends(get_current_user)]
Repo = Annotated[Repository, Depends(get_repository)]
