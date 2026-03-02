"""FastAPI dependency injection for the dashboard.

Provides reusable dependencies for authentication and data access
that can be injected into route handlers via ``Depends()``.
"""

from __future__ import annotations

from typing import Annotated

from fastapi import Depends, HTTPException, Request
from starlette.status import HTTP_303_SEE_OTHER

from src.dashboard.auth import SESSION_COOKIE_NAME, session_signer
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
    """Extract and verify the session cookie via the database.

    Flow:
    1. Read ``session_id`` cookie.
    2. Unsign it to get the raw session id.
    3. Look up the session in the database (``repo.get_session_by_id``).
    4. If any step fails, redirect to ``/login``.
    5. Touch ``last_active_at`` for activity tracking.
    6. Return a dict with ``user_id``, ``username``, ``display_name``,
       and ``session_id``.
    """
    token = request.cookies.get(SESSION_COOKIE_NAME)
    if not token:
        raise HTTPException(
            status_code=HTTP_303_SEE_OTHER,
            headers={"Location": "/login"},
            detail="Not authenticated",
        )

    session_id = session_signer.unsign_session_id(token)
    if session_id is None:
        raise HTTPException(
            status_code=HTTP_303_SEE_OTHER,
            headers={"Location": "/login"},
            detail="Invalid session",
        )

    repo: Repository = request.app.state.repo
    session = await repo.get_session_by_id(session_id)
    if session is None or session.user is None:
        raise HTTPException(
            status_code=HTTP_303_SEE_OTHER,
            headers={"Location": "/login"},
            detail="Session expired",
        )

    # Touch activity timestamp (fire-and-forget)
    await repo.update_session_activity(session_id)

    return {
        "user_id": session.user.id,
        "username": session.user.username,
        "display_name": session.user.display_name or session.user.username,
        "session_id": session_id,
    }


# ────────────────────────────────────────────────────────────
#  Type aliases for cleaner route signatures
# ────────────────────────────────────────────────────────────

CurrentUser = Annotated[dict, Depends(get_current_user)]
Repo = Annotated[Repository, Depends(get_repository)]
