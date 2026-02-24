"""Duplicate product prevention via ASIN cooldown checks.

Ensures the same product is not published to Telegram more than
once within a configurable cooldown period (default: 30 days).
"""

from __future__ import annotations

import logging

from src.amazon.models import Product
from src.database.repository import Repository

logger = logging.getLogger(__name__)


class DuplicateChecker:
    """Prevents publishing the same product (by ASIN) within a cooldown period."""

    def __init__(self, repository: Repository) -> None:
        self._repo = repository

    async def is_duplicate(self, asin: str) -> bool:
        """Check if *asin* was published within the cooldown window.

        The cooldown duration is read from the
        ``publishing.duplicate_cooldown_days`` setting (fallback: 30 days).
        """
        cooldown = await self._repo.get_setting_int(
            "publishing.duplicate_cooldown_days"
        )
        if cooldown <= 0:
            cooldown = 30
        return await self._repo.is_product_published(asin, cooldown)

    async def filter_new(self, products: list[Product]) -> list[Product]:
        """Return only products that have **not** been published recently.

        Preserves the original ordering.
        """
        result: list[Product] = []
        for product in products:
            if await self.is_duplicate(product.asin):
                logger.debug("Skipping duplicate ASIN: %s", product.asin)
            else:
                result.append(product)

        if len(result) < len(products):
            logger.info(
                "Filtered %d duplicates, %d new products remaining.",
                len(products) - len(result),
                len(result),
            )
        return result
