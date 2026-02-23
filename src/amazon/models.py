"""Product data model shared across all Amazon data sources.

A lightweight dataclass used by both the PA API client and the web scraper
to represent a single Amazon product with computed display helpers.
"""

from __future__ import annotations

from dataclasses import asdict, dataclass, field
from datetime import datetime, timezone


@dataclass
class Product:
    """Represents a single Amazon product with pricing and metadata."""

    asin: str
    title: str
    brand: str = ""
    original_price: float = 0.0
    current_price: float = 0.0
    currency: str = "SAR"
    savings_percent: float = 0.0
    savings_amount: float = 0.0
    features: list[str] = field(default_factory=list)
    image_url: str = ""
    affiliate_url: str = ""
    is_prime: bool = False
    rating: float = 0.0
    reviews_count: int = 0
    category: str = ""
    keyword_used: str = ""
    deal_badge: str = ""          # e.g. "Limited time deal"
    deal_end_time: str = ""       # ISO 8601, e.g. "2026-03-07T20:45:00Z"
    is_deal: bool = False

    # ── Computed properties ─────────────────────────────────

    @property
    def deal_display(self) -> str:
        """Arabic deal badge with remaining time, empty if no deal."""
        if not self.is_deal:
            return ""
        label = "🔥 عرض لفترة محدودة"
        if self.deal_end_time:
            try:
                end = datetime.fromisoformat(
                    self.deal_end_time.replace("Z", "+00:00")
                )
                remaining = end - datetime.now(timezone.utc)
                if remaining.total_seconds() > 0:
                    days = remaining.days
                    hours = remaining.seconds // 3600
                    if days > 0:
                        label += f" ⏰ متبقي {days} يوم و {hours} ساعة"
                    elif hours > 0:
                        mins = (remaining.seconds % 3600) // 60
                        label += f" ⏰ متبقي {hours} ساعة و {mins} دقيقة"
                    else:
                        mins = remaining.seconds // 60
                        label += f" ⏰ متبقي {mins} دقيقة"
            except (ValueError, TypeError):
                pass
        return label

    @property
    def has_discount(self) -> bool:
        """True when the product has a non-zero discount."""
        return self.savings_percent > 0

    @property
    def features_formatted(self) -> str:
        """First 5 features as a bullet list with check-mark emoji."""
        if not self.features:
            return ""
        return "\n".join(f"✅ {f}" for f in self.features[:5])

    @property
    def prime_badge(self) -> str:
        """Arabic Prime shipping badge, empty if not Prime."""
        return "🚀 شحن برايم" if self.is_prime else ""

    @property
    def stars_display(self) -> str:
        """Star emoji string matching the rating, e.g. '⭐⭐⭐⭐ (4.2)'."""
        if not self.rating:
            return ""
        stars = "⭐" * int(self.rating)
        return f"{stars} ({self.rating})"

    # ── Serialisation ───────────────────────────────────────

    def to_dict(self) -> dict:
        """Return all fields as a plain dict (for DB persistence)."""
        return asdict(self)
