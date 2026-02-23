"""Product dataclass for Amazon PA API 5.0 responses."""

from __future__ import annotations

from dataclasses import dataclass, field
from datetime import datetime
from typing import Optional


# Currency symbol mapping
_CURRENCY_SYMBOLS: dict[str, str] = {
    "USD": "$",
    "SAR": "ر.س",
    "AED": "د.إ",
    "GBP": "£",
    "EUR": "€",
}


@dataclass
class Product:
    """Represents a single Amazon product with pricing and metadata.

    Attributes:
        asin: Amazon Standard Identification Number.
        title: Product display title.
        url: Affiliate link to the product page.
        image_url: URL of the primary product image.
        original_price: Price before discount (saving basis).
        current_price: Current listing price.
        currency: ISO 4217 currency code.
        savings_amount: Absolute discount value.
        savings_percent: Discount as an integer percentage.
        features: Key product feature bullet points.
        brand: Brand / manufacturer name.
        category: Search category the product was found in.
        is_prime: Whether the product is Prime-eligible.
        fetched_at: UTC timestamp when this product was fetched.
    """

    asin: str
    title: str
    url: str
    image_url: Optional[str] = None
    original_price: Optional[float] = None
    current_price: Optional[float] = None
    currency: str = "USD"
    savings_amount: Optional[float] = None
    savings_percent: Optional[int] = None
    features: list[str] = field(default_factory=list)
    brand: Optional[str] = None
    category: Optional[str] = None
    is_prime: bool = False
    fetched_at: datetime = field(default_factory=datetime.utcnow)

    # ------------------------------------------------------------------
    # Properties
    # ------------------------------------------------------------------

    @property
    def has_discount(self) -> bool:
        """Return True if the product has a valid discount."""
        return (
            self.original_price is not None
            and self.current_price is not None
            and self.original_price > self.current_price
        )

    @property
    def discount_display(self) -> str:
        """Return a human-readable discount string like ``'25%'``.

        Uses ``savings_percent`` when available; otherwise calculates it
        from the two price fields.
        """
        if self.savings_percent is not None:
            return f"{self.savings_percent}%"
        if self.has_discount:
            percent = int(
                (self.original_price - self.current_price)  # type: ignore[operator]
                / self.original_price  # type: ignore[operator]
                * 100
            )
            return f"{percent}%"
        return "0%"

    @property
    def price_display(self) -> str:
        """Return the current price formatted with its currency symbol.

        Example: ``'$29.99'`` or ``'ر.س 120.00'``.
        """
        symbol = _CURRENCY_SYMBOLS.get(self.currency, self.currency)
        if self.current_price is None:
            return f"{symbol}—"
        return f"{symbol}{self.current_price:,.2f}"

    # ------------------------------------------------------------------
    # Factory
    # ------------------------------------------------------------------

    @classmethod
    def from_api_response(cls, item: dict, partner_tag: str) -> Product:
        """Parse a single item from an Amazon PA API 5.0 SearchItems response.

        Args:
            item: One element of the ``SearchResult.Items`` list.
            partner_tag: Amazon Associates partner tag (unused in parsing
                but reserved for URL rewriting if needed).

        Returns:
            A fully-populated ``Product`` instance.

        The parser navigates the deeply-nested PA API response using
        ``.get()`` at every level so that missing keys never raise.
        """
        # -- basic identifiers ----------------------------------------
        asin: str = item.get("ASIN", "")
        detail_url: str = item.get("DetailPageURL", "")

        # -- item info -------------------------------------------------
        item_info: dict = item.get("ItemInfo", {})

        title_block: dict = item_info.get("Title", {})
        title: str = title_block.get("DisplayValue", "")

        brand: Optional[str] = (
            item_info.get("ByLineInfo", {})
            .get("Brand", {})
            .get("DisplayValue")
        )

        features_block: dict = item_info.get("Features", {})
        features: list[str] = features_block.get("DisplayValues", [])[:5]

        # -- images ----------------------------------------------------
        images: dict = item.get("Images", {})
        image_url: Optional[str] = (
            images.get("Primary", {}).get("Large", {}).get("URL")
        )

        # -- offers / pricing ------------------------------------------
        offers: dict = item.get("Offers", {})
        listings: list[dict] = offers.get("Listings", [])
        listing: dict = listings[0] if listings else {}

        price_block: dict = listing.get("Price", {})
        current_price: Optional[float] = price_block.get("Amount")
        currency: str = price_block.get("Currency", "USD")

        saving_basis: dict = listing.get("SavingBasis", {})
        original_price: Optional[float] = saving_basis.get("Amount")

        delivery: dict = listing.get("DeliveryInfo", {})
        is_prime: bool = delivery.get("IsPrimeEligible", False)

        # -- calculated savings ----------------------------------------
        savings_amount: Optional[float] = None
        savings_percent: Optional[int] = None

        if original_price is not None and current_price is not None:
            if original_price > current_price:
                savings_amount = round(original_price - current_price, 2)
                savings_percent = int(
                    (savings_amount / original_price) * 100
                )

        return cls(
            asin=asin,
            title=title,
            url=detail_url,
            image_url=image_url,
            original_price=original_price,
            current_price=current_price,
            currency=currency,
            savings_amount=savings_amount,
            savings_percent=savings_percent,
            features=features,
            brand=brand,
            is_prime=is_prime,
        )
