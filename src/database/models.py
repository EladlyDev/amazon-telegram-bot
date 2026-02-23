"""SQLAlchemy 2.0 ORM models for the Amazon-Telegram bot.

Defines all database tables, relationships, and default seed data
(settings and post templates).
"""

from datetime import datetime

from sqlalchemy import (
    Boolean,
    DateTime,
    Float,
    ForeignKey,
    Index,
    Integer,
    String,
    Text,
)
from sqlalchemy.orm import (
    DeclarativeBase,
    Mapped,
    mapped_column,
    relationship,
)


# ── Base ────────────────────────────────────────────────────


class Base(DeclarativeBase):
    """DeclarativeBase for all ORM models."""

    pass


# ── Models ──────────────────────────────────────────────────


class Category(Base):
    """Product category with search parameters and keyword rotation tracking."""

    __tablename__ = "categories"

    id: Mapped[int] = mapped_column(Integer, primary_key=True, autoincrement=True)
    name: Mapped[str] = mapped_column(String(255), nullable=False)
    name_ar: Mapped[str | None] = mapped_column(String(255), nullable=True)
    amazon_search_index: Mapped[str] = mapped_column(String(100), default="All")
    amazon_browse_node: Mapped[str | None] = mapped_column(String(50), nullable=True)
    min_price: Mapped[float] = mapped_column(Float, default=0)
    max_price: Mapped[float] = mapped_column(Float, default=0)
    min_discount_percent: Mapped[float] = mapped_column(Float, default=0)
    require_prime: Mapped[bool] = mapped_column(Boolean, default=False)
    require_image: Mapped[bool] = mapped_column(Boolean, default=True)
    is_active: Mapped[bool] = mapped_column(Boolean, default=True)
    sort_order: Mapped[int] = mapped_column(Integer, default=0)
    rotation_index: Mapped[int] = mapped_column(Integer, default=0)
    created_at: Mapped[datetime] = mapped_column(DateTime, default=datetime.utcnow)
    updated_at: Mapped[datetime] = mapped_column(
        DateTime, default=datetime.utcnow, onupdate=datetime.utcnow
    )

    keywords: Mapped[list["Keyword"]] = relationship(
        "Keyword",
        back_populates="category",
        cascade="all, delete-orphan",
        order_by="Keyword.sort_order",
    )


class Keyword(Base):
    """Search keyword belonging to a category, with usage statistics."""

    __tablename__ = "keywords"

    id: Mapped[int] = mapped_column(Integer, primary_key=True, autoincrement=True)
    category_id: Mapped[int] = mapped_column(
        Integer, ForeignKey("categories.id", ondelete="CASCADE"), nullable=False
    )
    keyword: Mapped[str] = mapped_column(String(255), nullable=False)
    is_active: Mapped[bool] = mapped_column(Boolean, default=True)
    sort_order: Mapped[int] = mapped_column(Integer, default=0)
    last_used_at: Mapped[datetime | None] = mapped_column(DateTime, nullable=True)
    total_uses: Mapped[int] = mapped_column(Integer, default=0)
    total_products_found: Mapped[int] = mapped_column(Integer, default=0)
    created_at: Mapped[datetime] = mapped_column(DateTime, default=datetime.utcnow)

    category: Mapped["Category"] = relationship("Category", back_populates="keywords")


class PublishedProduct(Base):
    """Record of a product that was published (or failed) to Telegram."""

    __tablename__ = "published_products"

    __table_args__ = (
        Index("ix_published_products_asin_published_at", "asin", "published_at"),
    )

    id: Mapped[int] = mapped_column(Integer, primary_key=True, autoincrement=True)
    asin: Mapped[str] = mapped_column(String(20), nullable=False, index=True)
    title: Mapped[str | None] = mapped_column(String(500))
    brand: Mapped[str | None] = mapped_column(String(255))
    original_price: Mapped[float | None] = mapped_column(Float)
    current_price: Mapped[float | None] = mapped_column(Float)
    currency: Mapped[str] = mapped_column(String(10), default="SAR")
    savings_percent: Mapped[float | None] = mapped_column(Float)
    savings_amount: Mapped[float | None] = mapped_column(Float)
    features: Mapped[str | None] = mapped_column(Text)
    image_url: Mapped[str | None] = mapped_column(String(1000))
    affiliate_url: Mapped[str | None] = mapped_column(String(1000))
    category_name: Mapped[str | None] = mapped_column(String(255))
    keyword_used: Mapped[str | None] = mapped_column(String(255))
    is_prime: Mapped[bool] = mapped_column(Boolean, default=False)
    rating: Mapped[float | None] = mapped_column(Float)
    reviews_count: Mapped[int | None] = mapped_column(Integer)
    status: Mapped[str] = mapped_column(String(50), default="published")
    telegram_message_id: Mapped[int | None] = mapped_column(Integer, nullable=True)
    error_message: Mapped[str | None] = mapped_column(Text, nullable=True)
    published_at: Mapped[datetime] = mapped_column(DateTime, default=datetime.utcnow)
    created_at: Mapped[datetime] = mapped_column(DateTime, default=datetime.utcnow)


class Schedule(Base):
    """Publishing schedule configuration (fixed times or interval-based)."""

    __tablename__ = "schedules"

    id: Mapped[int] = mapped_column(Integer, primary_key=True, autoincrement=True)
    schedule_type: Mapped[str] = mapped_column(String(50), default="fixed_times")
    fixed_times: Mapped[str] = mapped_column(
        Text, default='["09:00","12:00","15:00","18:00","21:00"]'
    )
    interval_minutes: Mapped[int] = mapped_column(Integer, default=180)
    products_per_batch: Mapped[int] = mapped_column(Integer, default=1)
    timezone: Mapped[str] = mapped_column(String(50), default="Asia/Riyadh")
    is_active: Mapped[bool] = mapped_column(Boolean, default=True)
    created_at: Mapped[datetime] = mapped_column(DateTime, default=datetime.utcnow)
    updated_at: Mapped[datetime] = mapped_column(
        DateTime, default=datetime.utcnow, onupdate=datetime.utcnow
    )


class PostTemplate(Base):
    """Telegram message template with variable placeholders."""

    __tablename__ = "post_templates"

    id: Mapped[int] = mapped_column(Integer, primary_key=True, autoincrement=True)
    name: Mapped[str] = mapped_column(String(255), nullable=False)
    description: Mapped[str | None] = mapped_column(String(500), nullable=True)
    body: Mapped[str] = mapped_column(Text, nullable=False)
    parse_mode: Mapped[str] = mapped_column(String(20), default="HTML")
    include_image: Mapped[bool] = mapped_column(Boolean, default=True)
    is_active: Mapped[bool] = mapped_column(Boolean, default=False)
    created_at: Mapped[datetime] = mapped_column(DateTime, default=datetime.utcnow)
    updated_at: Mapped[datetime] = mapped_column(
        DateTime, default=datetime.utcnow, onupdate=datetime.utcnow
    )


class SystemLog(Base):
    """Structured log entry for dashboard visibility."""

    __tablename__ = "system_logs"

    id: Mapped[int] = mapped_column(Integer, primary_key=True, autoincrement=True)
    level: Mapped[str] = mapped_column(String(20))
    component: Mapped[str] = mapped_column(String(50))
    action: Mapped[str] = mapped_column(String(100))
    message: Mapped[str] = mapped_column(Text)
    details: Mapped[str | None] = mapped_column(Text, nullable=True)
    created_at: Mapped[datetime] = mapped_column(DateTime, default=datetime.utcnow)


class Setting(Base):
    """Key-value configuration store, editable from the dashboard."""

    __tablename__ = "settings"

    key: Mapped[str] = mapped_column(String(255), primary_key=True)
    value: Mapped[str] = mapped_column(Text, nullable=False)
    value_type: Mapped[str] = mapped_column(String(20), default="string")
    description: Mapped[str | None] = mapped_column(String(500), nullable=True)
    group_name: Mapped[str] = mapped_column(String(100), default="general")
    updated_at: Mapped[datetime] = mapped_column(
        DateTime, default=datetime.utcnow, onupdate=datetime.utcnow
    )


# ── Default seed data ──────────────────────────────────────

DEFAULT_SETTINGS: list[dict[str, str]] = [
    # Amazon
    {
        "key": "amazon.marketplace",
        "value": "www.amazon.sa",
        "value_type": "string",
        "group_name": "amazon",
        "description": "Amazon marketplace domain",
    },
    {
        "key": "amazon.partner_tag",
        "value": "",
        "value_type": "string",
        "group_name": "amazon",
        "description": "Amazon Associates partner tag",
    },
    {
        "key": "amazon.data_source",
        "value": "scraper",
        "value_type": "string",
        "group_name": "amazon",
        "description": "Data source: 'scraper' or 'pa_api'",
    },
    {
        "key": "amazon.request_delay_seconds",
        "value": "2.0",
        "value_type": "float",
        "group_name": "amazon",
        "description": "Delay between Amazon requests in seconds",
    },
    # Telegram
    {
        "key": "telegram.channel_id",
        "value": "",
        "value_type": "string",
        "group_name": "telegram",
        "description": "Telegram channel ID for publishing",
    },
    {
        "key": "telegram.admin_chat_id",
        "value": "",
        "value_type": "string",
        "group_name": "telegram",
        "description": "Admin chat ID for error notifications",
    },
    {
        "key": "telegram.send_errors_to_admin",
        "value": "true",
        "value_type": "bool",
        "group_name": "telegram",
        "description": "Send error alerts to admin chat",
    },
    # Publishing
    {
        "key": "publishing.duplicate_cooldown_days",
        "value": "30",
        "value_type": "int",
        "group_name": "publishing",
        "description": "Days before a product can be re-published",
    },
    {
        "key": "publishing.max_retries",
        "value": "3",
        "value_type": "int",
        "group_name": "publishing",
        "description": "Max retry attempts for failed publishes",
    },
    {
        "key": "publishing.retry_delay_seconds",
        "value": "30",
        "value_type": "int",
        "group_name": "publishing",
        "description": "Delay between publish retries in seconds",
    },
    # Bot
    {
        "key": "bot.is_running",
        "value": "false",
        "value_type": "bool",
        "group_name": "bot",
        "description": "Whether the bot scheduler is active",
    },
    {
        "key": "bot.last_publish_at",
        "value": "",
        "value_type": "string",
        "group_name": "bot",
        "description": "Timestamp of last successful publish",
    },
    {
        "key": "bot.total_published",
        "value": "0",
        "value_type": "int",
        "group_name": "bot",
        "description": "Total number of products published",
    },
]

DEFAULT_TEMPLATES: list[dict] = [
    {
        "name": "تفصيلي",
        "description": "قالب تفصيلي مع جميع المعلومات",
        "body": (
            "🔥 <b>{title}</b>\n"
            "\n"
            "🏷️ {brand}\n"
            "\n"
            "💰 السعر: <s>{original_price} {currency}</s>  ➜  <b>{current_price} {currency}</b>\n"
            "💚 وفّر {savings_percent}% ({savings_amount} {currency})\n"
            "\n"
            "{deal_display}\n"
            "\n"
            "{prime_badge}\n"
            "\n"
            "{features}\n"
            "\n"
            "{rating}\n"
            "\n"
            '🛒 <a href="{url}">اطلب الآن من أمازون</a>'
        ),
        "parse_mode": "HTML",
        "include_image": True,
        "is_active": True,
    },
    {
        "name": "مختصر",
        "description": "قالب مختصر وسريع",
        "body": (
            "🔥 <b>{title}</b>\n"
            "\n"
            "💰 <s>{original_price}</s> ➜ <b>{current_price} {currency}</b> (-{savings_percent}%)\n"
            "\n"
            "{deal_display}\n"
            "\n"
            "{prime_badge}\n"
            "\n"
            '🛒 <a href="{url}">اطلب الآن</a>'
        ),
        "parse_mode": "HTML",
        "include_image": True,
        "is_active": False,
    },
]
