"""Abstract base class for Amazon data sources.

Defines the interface that both the PA API client and the web scraper
must implement so the engine can swap between them transparently.
"""

from __future__ import annotations

from abc import ABC, abstractmethod

from src.amazon.models import Product


class AmazonClient(ABC):
    """Interface for fetching products from Amazon."""

    @abstractmethod
    async def search_products(
        self,
        keywords: str,
        search_index: str = "All",
        item_count: int = 10,
        min_price: int | None = None,
        max_price: int | None = None,
        browse_node: str | None = None,
    ) -> list[Product]:
        """Search Amazon and return a list of products."""
        ...

    async def close(self) -> None:
        """Release any held resources (HTTP clients, etc.)."""
        pass
