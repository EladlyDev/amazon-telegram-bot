"""Round-robin rotation across categories and their keywords.

Ensures even distribution of Amazon searches by cycling through
categories in order, and within each category rotating through
its active keywords.
"""

from __future__ import annotations

import logging

from src.database.models import Category, Keyword
from src.database.repository import Repository

logger = logging.getLogger(__name__)


class CategoryRotator:
    """Round-robin rotator for categories and keywords.

    Cycle example with 2 categories
    (Electronics: [kw1, kw2], Fashion: [kw3])::

        Call 1 → Electronics, kw1
        Call 2 → Electronics, kw2
        Call 3 → Fashion, kw3
        Call 4 → Electronics, kw1  (cycle restarts)
    """

    def __init__(self, repository: Repository) -> None:
        self._repo = repository
        self._categories: list[Category] = []
        self._current_cat_index: int = 0
        self._initialized: bool = False

    # ────────────────────────────────────────────────────────
    #  Loading
    # ────────────────────────────────────────────────────────

    async def _ensure_loaded(self) -> None:
        """Lazy-load active categories on first access."""
        if not self._initialized:
            await self.reload()

    async def reload(self) -> None:
        """(Re)load active categories from the database."""
        self._categories = await self._repo.get_active_categories()
        self._current_cat_index = 0
        self._initialized = True
        logger.info(
            "Rotator loaded %d active categories.", len(self._categories)
        )

    # ────────────────────────────────────────────────────────
    #  Public API
    # ────────────────────────────────────────────────────────

    async def get_next(self) -> tuple[Category | None, Keyword | None]:
        """Return the next ``(category, keyword)`` pair without advancing.

        Returns ``(None, None)`` if no active categories or keywords exist.
        """
        await self._ensure_loaded()

        if not self._categories:
            return None, None

        # Safety: wrap index
        self._current_cat_index %= len(self._categories)
        category = self._categories[self._current_cat_index]

        # Get active keywords for this category, sorted by sort_order
        active_keywords = [kw for kw in category.keywords if kw.is_active]
        if not active_keywords:
            logger.warning(
                "Category '%s' has no active keywords.", category.name
            )
            return category, None

        active_keywords.sort(key=lambda k: k.sort_order)

        # Use rotation_index to pick the current keyword
        kw_index = category.rotation_index % len(active_keywords)
        keyword = active_keywords[kw_index]

        logger.debug(
            "Next rotation: [%s] keyword '%s' (index %d/%d)",
            category.name,
            keyword.keyword,
            kw_index + 1,
            len(active_keywords),
        )
        return category, keyword

    async def advance(self) -> None:
        """Advance to the next keyword/category after a successful use.

        1. Increment the current category's ``rotation_index``.
        2. Persist to the database.
        3. If all keywords in the category have been used, reset to 0
           and move to the next category.
        """
        await self._ensure_loaded()

        if not self._categories:
            return

        self._current_cat_index %= len(self._categories)
        category = self._categories[self._current_cat_index]

        active_keywords = [kw for kw in category.keywords if kw.is_active]
        if not active_keywords:
            # Skip to next category
            self._current_cat_index = (
                (self._current_cat_index + 1) % len(self._categories)
            )
            return

        new_index = category.rotation_index + 1

        if new_index >= len(active_keywords):
            # All keywords in this category used — reset and move on
            new_index = 0
            self._current_cat_index = (
                (self._current_cat_index + 1) % len(self._categories)
            )
            logger.debug(
                "Category '%s' rotation complete, moving to next.",
                category.name,
            )

        category.rotation_index = new_index
        await self._repo.update_rotation_index(category.id, new_index)
