"""In-memory sliding-window rate limiter.

No Redis required — suitable for a single-process dashboard.
Tracks request timestamps per key (typically an IP address) and
enforces ``max_requests`` within a rolling ``window_seconds`` window.
"""

from __future__ import annotations

import asyncio
import logging
import time

logger = logging.getLogger(__name__)


class RateLimiter:
    """Sliding-window rate limiter backed by a plain ``dict``.

    Thread-safe via :class:`asyncio.Lock`.

    Usage::

        if await login_limiter.is_rate_limited(ip, max_requests=5, window_seconds=300):
            return JSONResponse({"error": "Too many attempts"}, status_code=429)
    """

    def __init__(self) -> None:
        self._requests: dict[str, list[float]] = {}
        self._lock = asyncio.Lock()

    async def is_rate_limited(
        self, key: str, max_requests: int, window_seconds: int
    ) -> bool:
        """Return ``True`` if *key* has exceeded the rate limit.

        Example: ``is_rate_limited("192.168.1.1", 5, 300)`` ⟶ max 5
        requests per 5 minutes per IP.
        """
        async with self._lock:
            now = time.time()

            if key not in self._requests:
                self._requests[key] = []

            # Evict timestamps outside the window
            self._requests[key] = [
                ts for ts in self._requests[key]
                if now - ts < window_seconds
            ]

            if len(self._requests[key]) >= max_requests:
                return True

            self._requests[key].append(now)
            return False

    async def cleanup(self) -> None:
        """Remove entries that have had no activity for > 1 hour."""
        async with self._lock:
            now = time.time()
            expired = [
                k for k, timestamps in self._requests.items()
                if not timestamps or (now - max(timestamps)) > 3600
            ]
            for k in expired:
                del self._requests[k]
            if expired:
                logger.debug("Rate-limiter cleanup: removed %d stale keys.", len(expired))


# ── Module-level instances ─────────────────────────────────

login_limiter = RateLimiter()
recovery_limiter = RateLimiter()
