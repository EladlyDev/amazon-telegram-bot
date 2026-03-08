"""Global keyword publish queue.

Instead of per-category round-robin, this provides a flat, ordered
queue of all active keywords from active categories.  The admin can
drag-and-drop to reorder the global queue via the dashboard.
"""

from __future__ import annotations

import logging
import random as _random

from src.database.models import Category, Keyword
from src.database.repository import Repository

logger = logging.getLogger(__name__)

_PUBLISH_INDEX_KEY = "publishing.queue_index"
_KEYWORD_ORDER_KEY = "publishing.keyword_order"


class CategoryRotator:
    """Global keyword queue rotator.

    Loads all active keywords from active categories as a flat list,
    sorted by ``Keyword.sort_order``.  Supports manual (ordered) and
    random modes via a global ``publishing.keyword_order`` setting.
    """

    def __init__(self, repository: Repository) -> None:
        self._repo = repository
        self._queue: list[Keyword] = []
        self._current_index: int = 0
        self._order_mode: str = "sort_order"

    # ────────────────────────────────────────────────────────
    #  Loading
    # ────────────────────────────────────────────────────────

    async def _ensure_loaded(self) -> None:
        """Reload queue from DB on every call to pick up changes."""
        await self.reload()

    async def reload(self) -> None:
        """(Re)load the global publish queue from the database."""
        self._queue = await self._repo.get_global_publish_queue()
        self._order_mode = (
            await self._repo.get_setting(_KEYWORD_ORDER_KEY) or "sort_order"
        )

        # Read persisted index
        idx_str = await self._repo.get_setting(_PUBLISH_INDEX_KEY)
        try:
            self._current_index = int(idx_str) if idx_str else 0
        except (ValueError, TypeError):
            self._current_index = 0

        if self._queue:
            self._current_index %= len(self._queue)

        logger.debug(
            "Queue loaded: %d keywords, mode=%s, index=%d.",
            len(self._queue),
            self._order_mode,
            self._current_index,
        )

    # ────────────────────────────────────────────────────────
    #  Public API
    # ────────────────────────────────────────────────────────

    async def get_next(self) -> tuple[Category | None, Keyword | None]:
        """Return the next ``(category, keyword)`` pair without advancing.

        Returns ``(None, None)`` if the queue is empty.
        """
        await self._ensure_loaded()

        if not self._queue:
            return None, None

        if self._order_mode == "random":
            keyword = _random.choice(self._queue)
        else:
            keyword = self._queue[self._current_index]

        category = keyword.category

        logger.debug(
            "Next publish: '%s' from [%s] (pos %d/%d, mode=%s)",
            keyword.keyword,
            category.name if category else "?",
            self._current_index + 1,
            len(self._queue),
            self._order_mode,
        )
        return category, keyword

    async def advance(self) -> None:
        """Advance to the next keyword after a successful publish."""
        await self._ensure_loaded()

        if not self._queue:
            return

        new_index = (self._current_index + 1) % len(self._queue)
        self._current_index = new_index
        await self._repo.set_setting(_PUBLISH_INDEX_KEY, str(new_index))

        logger.debug(
            "Queue advanced to index %d/%d.",
            new_index + 1,
            len(self._queue),
        )
