"""In-memory store for pending changes awaiting OTP verification.

Keeps proposed changes in RAM until the user submits the matching OTP.
Entries auto-expire after *ttl_seconds* (default 5 minutes) —
losing them on restart is fine because OTP codes expire too.
"""

from __future__ import annotations

import logging
import time

logger = logging.getLogger(__name__)


class PendingChangeStore:
    """Temporarily holds proposed changes keyed by ``(user_id, purpose)``.

    Only one pending change per (user, purpose) at a time — a new
    request replaces the previous one.
    """

    def __init__(self, ttl_seconds: int = 300) -> None:
        self._store: dict[tuple[int, str], dict] = {}
        self._ttl = ttl_seconds

    # ── Store methods ──────────────────────────────────────

    def store_password_change(self, user_id: int, new_password: str) -> None:
        """Store a pending password change."""
        self._store[(user_id, "password_change")] = {
            "new_password": new_password,
            "created_at": time.time(),
        }

    def store_settings_change(
        self,
        user_id: int,
        changes: dict[str, str],
        current_values: dict[str, str],
    ) -> None:
        """Store pending sensitive-settings changes with their current values."""
        self._store[(user_id, "settings_change")] = {
            "changes": changes,
            "current_values": current_values,
            "created_at": time.time(),
        }

    def store_username_change(self, user_id: int, new_username: str) -> None:
        """Store a pending username change."""
        self._store[(user_id, "username_change")] = {
            "new_username": new_username,
            "created_at": time.time(),
        }

    # ── Retrieve / query ───────────────────────────────────

    def retrieve(self, user_id: int, purpose: str) -> dict | None:
        """Retrieve **and remove** a pending change.

        Returns ``None`` if expired or not found.
        """
        key = (user_id, purpose)
        entry = self._store.pop(key, None)
        if not entry:
            return None
        if time.time() - entry["created_at"] > self._ttl:
            return None  # expired
        return entry

    def has_pending(self, user_id: int, purpose: str) -> bool:
        """Check whether a pending change exists (without consuming it)."""
        key = (user_id, purpose)
        entry = self._store.get(key)
        if not entry:
            return False
        if time.time() - entry["created_at"] > self._ttl:
            del self._store[key]
            return False
        return True

    # ── Maintenance ────────────────────────────────────────

    def cleanup(self) -> None:
        """Remove all expired entries."""
        now = time.time()
        expired = [
            k for k, v in self._store.items()
            if now - v["created_at"] > self._ttl
        ]
        for k in expired:
            del self._store[k]
        if expired:
            logger.debug("OTP store cleanup: removed %d expired entries.", len(expired))


# ── Module-level singleton ─────────────────────────────────

pending_changes = PendingChangeStore()
