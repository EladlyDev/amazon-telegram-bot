"""Session-based authentication for the dashboard.

Uses ``itsdangerous`` signed cookies for stateless sessions.
Passwords are stored as plain text in ``.env`` — this is intentional
for a single-admin, self-hosted bot.
"""

from __future__ import annotations

import logging

from itsdangerous import BadSignature, SignatureExpired, URLSafeTimedSerializer

from src.config import settings

logger = logging.getLogger(__name__)

SESSION_COOKIE_NAME = "session"

# Default session lifetime: 24 hours
SESSION_MAX_AGE = 86400


class SessionManager:
    """Create and verify signed session tokens."""

    def __init__(self, secret_key: str) -> None:
        self._serializer = URLSafeTimedSerializer(secret_key)

    def create_session(self, username: str) -> str:
        """Create a signed session token containing the username."""
        return self._serializer.dumps({"username": username})

    def verify_session(
        self, token: str, max_age: int = SESSION_MAX_AGE
    ) -> dict | None:
        """Verify and decode a session token.

        Returns:
            The session payload ``{"username": "..."}`` or ``None``
            if the token is invalid, tampered with, or expired.
        """
        try:
            return self._serializer.loads(token, max_age=max_age)
        except SignatureExpired:
            logger.debug("Session token expired.")
            return None
        except BadSignature:
            logger.debug("Session token has invalid signature.")
            return None
        except Exception:
            return None


def verify_password(plain: str, stored: str) -> bool:
    """Compare a plain-text password against the stored value.

    For a single-admin bot the stored password is plain text from ``.env``.
    """
    return plain == stored


# ── Module-level singleton ──────────────────────────────────

session_manager = SessionManager(settings.dashboard_secret_key)
