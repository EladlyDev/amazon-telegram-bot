"""Repository providing all database CRUD operations.

Each method manages its own session lifecycle via ``get_session()``.
All queries use SQLAlchemy 2.0-style ``select()`` statements with
async/await throughout.
"""

from __future__ import annotations

import logging
from datetime import datetime, timedelta
from typing import Any

from sqlalchemy import delete, func, select, update, case, literal
from sqlalchemy.orm import selectinload

from src.database.connection import get_session
from src.database.models import (
    Category,
    Keyword,
    PostTemplate,
    PublishedProduct,
    Schedule,
    Setting,
    SystemLog,
)

logger = logging.getLogger(__name__)


class Repository:
    """Centralised data-access layer for every entity in the system."""

    # ────────────────────────────────────────────────────────
    #  CATEGORIES
    # ────────────────────────────────────────────────────────

    async def get_all_categories(
        self, include_inactive: bool = False
    ) -> list[Category]:
        """Return categories with eager-loaded keywords, ordered by sort_order."""
        async with get_session() as session:
            stmt = (
                select(Category)
                .options(selectinload(Category.keywords))
                .order_by(Category.sort_order)
            )
            if not include_inactive:
                stmt = stmt.where(Category.is_active.is_(True))
            result = await session.execute(stmt)
            return list(result.scalars().unique().all())

    async def get_category(self, category_id: int) -> Category | None:
        """Return a single category with its keywords, or None."""
        async with get_session() as session:
            stmt = (
                select(Category)
                .options(selectinload(Category.keywords))
                .where(Category.id == category_id)
            )
            result = await session.execute(stmt)
            return result.scalars().first()

    async def create_category(self, data: dict[str, Any]) -> Category:
        """Create and return a new category."""
        async with get_session() as session:
            category = Category(**data)
            session.add(category)
            await session.flush()
            await session.refresh(category, attribute_names=["id"])
            return category

    async def update_category(
        self, category_id: int, data: dict[str, Any]
    ) -> Category | None:
        """Update a category by id. Returns the updated object or None."""
        async with get_session() as session:
            stmt = (
                select(Category)
                .options(selectinload(Category.keywords))
                .where(Category.id == category_id)
            )
            result = await session.execute(stmt)
            category = result.scalars().first()
            if category is None:
                return None
            for key, value in data.items():
                setattr(category, key, value)
            await session.flush()
            await session.refresh(category)
            return category

    async def delete_category(self, category_id: int) -> bool:
        """Delete a category and its keywords (via cascade). Returns success."""
        async with get_session() as session:
            stmt = select(Category).where(Category.id == category_id)
            result = await session.execute(stmt)
            category = result.scalars().first()
            if category is None:
                return False
            await session.delete(category)
            return True

    async def get_active_categories(self) -> list[Category]:
        """Return active categories that have at least one active keyword."""
        async with get_session() as session:
            stmt = (
                select(Category)
                .options(selectinload(Category.keywords))
                .where(Category.is_active.is_(True))
                .where(
                    Category.id.in_(
                        select(Keyword.category_id).where(Keyword.is_active.is_(True))
                    )
                )
                .order_by(Category.sort_order)
            )
            result = await session.execute(stmt)
            return list(result.scalars().unique().all())

    async def update_rotation_index(
        self, category_id: int, new_index: int
    ) -> None:
        """Update the rotation_index for a category."""
        async with get_session() as session:
            await session.execute(
                update(Category)
                .where(Category.id == category_id)
                .values(rotation_index=new_index)
            )

    # ────────────────────────────────────────────────────────
    #  KEYWORDS
    # ────────────────────────────────────────────────────────

    async def get_keywords_for_category(
        self, category_id: int
    ) -> list[Keyword]:
        """Return all keywords for a given category, ordered by sort_order."""
        async with get_session() as session:
            stmt = (
                select(Keyword)
                .where(Keyword.category_id == category_id)
                .order_by(Keyword.sort_order)
            )
            result = await session.execute(stmt)
            return list(result.scalars().all())

    async def create_keyword(
        self, category_id: int, keyword: str, sort_order: int = 0
    ) -> Keyword:
        """Create a keyword attached to a category."""
        async with get_session() as session:
            kw = Keyword(
                category_id=category_id,
                keyword=keyword,
                sort_order=sort_order,
            )
            session.add(kw)
            await session.flush()
            await session.refresh(kw, attribute_names=["id"])
            return kw

    async def update_keyword(
        self, keyword_id: int, data: dict[str, Any]
    ) -> Keyword | None:
        """Update a keyword by id. Returns updated object or None."""
        async with get_session() as session:
            stmt = select(Keyword).where(Keyword.id == keyword_id)
            result = await session.execute(stmt)
            kw = result.scalars().first()
            if kw is None:
                return None
            for key, value in data.items():
                setattr(kw, key, value)
            await session.flush()
            await session.refresh(kw)
            return kw

    async def delete_keyword(self, keyword_id: int) -> bool:
        """Delete a keyword by id. Returns success."""
        async with get_session() as session:
            stmt = select(Keyword).where(Keyword.id == keyword_id)
            result = await session.execute(stmt)
            kw = result.scalars().first()
            if kw is None:
                return False
            await session.delete(kw)
            return True

    async def update_keyword_usage(
        self, keyword_id: int, products_found: int = 0
    ) -> None:
        """Record a keyword usage: stamp time, bump counters."""
        async with get_session() as session:
            stmt = select(Keyword).where(Keyword.id == keyword_id)
            result = await session.execute(stmt)
            kw = result.scalars().first()
            if kw is None:
                return
            kw.last_used_at = datetime.utcnow()
            kw.total_uses += 1
            kw.total_products_found += products_found
            await session.flush()

    # ────────────────────────────────────────────────────────
    #  PUBLISHED PRODUCTS
    # ────────────────────────────────────────────────────────

    async def save_published_product(
        self, product_data: dict[str, Any]
    ) -> PublishedProduct:
        """Persist a published (or failed) product record."""
        async with get_session() as session:
            product = PublishedProduct(**product_data)
            session.add(product)
            await session.flush()
            await session.refresh(product, attribute_names=["id"])
            return product

    async def is_product_published(
        self, asin: str, cooldown_days: int = 30
    ) -> bool:
        """Check if this ASIN was successfully published within the cooldown window."""
        async with get_session() as session:
            cutoff = datetime.utcnow() - timedelta(days=cooldown_days)
            stmt = (
                select(func.count())
                .select_from(PublishedProduct)
                .where(
                    PublishedProduct.asin == asin,
                    PublishedProduct.status == "published",
                    PublishedProduct.published_at >= cutoff,
                )
            )
            result = await session.execute(stmt)
            return (result.scalar() or 0) > 0

    async def get_published_products(
        self,
        limit: int = 50,
        offset: int = 0,
        status: str | None = None,
        date_from: datetime | None = None,
        date_to: datetime | None = None,
    ) -> list[PublishedProduct]:
        """Return published products, newest first, with optional filters."""
        async with get_session() as session:
            stmt = select(PublishedProduct).order_by(
                PublishedProduct.published_at.desc()
            )
            if status:
                stmt = stmt.where(PublishedProduct.status == status)
            if date_from:
                stmt = stmt.where(PublishedProduct.published_at >= date_from)
            if date_to:
                stmt = stmt.where(PublishedProduct.published_at <= date_to)
            stmt = stmt.limit(limit).offset(offset)
            result = await session.execute(stmt)
            return list(result.scalars().all())

    async def get_published_count(self, days: int | None = None) -> int:
        """Total published count, optionally limited to the last N days."""
        async with get_session() as session:
            stmt = select(func.count()).select_from(PublishedProduct)
            if days is not None:
                cutoff = datetime.utcnow() - timedelta(days=days)
                stmt = stmt.where(PublishedProduct.published_at >= cutoff)
            result = await session.execute(stmt)
            return result.scalar() or 0

    async def get_daily_stats(self, days: int = 7) -> list[dict[str, Any]]:
        """Per-day breakdown of published / failed counts for the last N days."""
        async with get_session() as session:
            cutoff = datetime.utcnow() - timedelta(days=days)
            date_col = func.date(PublishedProduct.published_at).label("date")
            stmt = (
                select(
                    date_col,
                    func.count().label("count"),
                    func.sum(
                        case(
                            (PublishedProduct.status == "published", 1),
                            else_=0,
                        )
                    ).label("success"),
                    func.sum(
                        case(
                            (PublishedProduct.status == "failed", 1),
                            else_=0,
                        )
                    ).label("failed"),
                )
                .where(PublishedProduct.published_at >= cutoff)
                .group_by(date_col)
                .order_by(date_col)
            )
            result = await session.execute(stmt)
            db_rows = {row.date: row for row in result.all()}

            # Build a complete list including zero-count days
            stats: list[dict[str, Any]] = []
            today = datetime.utcnow().date()
            for i in range(days - 1, -1, -1):
                d = (today - timedelta(days=i)).isoformat()
                if d in db_rows:
                    r = db_rows[d]
                    stats.append(
                        {
                            "date": d,
                            "count": r.count,
                            "success": r.success or 0,
                            "failed": r.failed or 0,
                        }
                    )
                else:
                    stats.append(
                        {"date": d, "count": 0, "success": 0, "failed": 0}
                    )
            return stats

    # ────────────────────────────────────────────────────────
    #  SCHEDULE
    # ────────────────────────────────────────────────────────

    async def get_active_schedule(self) -> Schedule | None:
        """Return the active schedule, if any."""
        async with get_session() as session:
            stmt = select(Schedule).where(Schedule.is_active.is_(True))
            result = await session.execute(stmt)
            return result.scalars().first()

    async def get_schedule(self) -> Schedule | None:
        """Return the first (and only) schedule record."""
        async with get_session() as session:
            stmt = select(Schedule)
            result = await session.execute(stmt)
            return result.scalars().first()

    async def update_schedule(self, data: dict[str, Any]) -> Schedule:
        """Update (or create) the single schedule record."""
        async with get_session() as session:
            stmt = select(Schedule)
            result = await session.execute(stmt)
            schedule = result.scalars().first()
            if schedule is None:
                schedule = Schedule(**data)
                session.add(schedule)
            else:
                for key, value in data.items():
                    setattr(schedule, key, value)
            await session.flush()
            await session.refresh(schedule)
            return schedule

    # ────────────────────────────────────────────────────────
    #  TEMPLATES
    # ────────────────────────────────────────────────────────

    async def get_all_templates(self) -> list[PostTemplate]:
        """Return every post template."""
        async with get_session() as session:
            stmt = select(PostTemplate).order_by(PostTemplate.id)
            result = await session.execute(stmt)
            return list(result.scalars().all())

    async def get_active_template(self) -> PostTemplate | None:
        """Return the currently active template, or None."""
        async with get_session() as session:
            stmt = select(PostTemplate).where(PostTemplate.is_active.is_(True))
            result = await session.execute(stmt)
            return result.scalars().first()

    async def create_template(self, data: dict[str, Any]) -> PostTemplate:
        """Create a new post template."""
        async with get_session() as session:
            template = PostTemplate(**data)
            session.add(template)
            await session.flush()
            await session.refresh(template, attribute_names=["id"])
            return template

    async def update_template(
        self, template_id: int, data: dict[str, Any]
    ) -> PostTemplate | None:
        """Update a template by id. Returns updated object or None."""
        async with get_session() as session:
            stmt = select(PostTemplate).where(PostTemplate.id == template_id)
            result = await session.execute(stmt)
            template = result.scalars().first()
            if template is None:
                return None
            for key, value in data.items():
                setattr(template, key, value)
            await session.flush()
            await session.refresh(template)
            return template

    async def delete_template(self, template_id: int) -> bool:
        """Delete a template by id. Returns success."""
        async with get_session() as session:
            stmt = select(PostTemplate).where(PostTemplate.id == template_id)
            result = await session.execute(stmt)
            template = result.scalars().first()
            if template is None:
                return False
            await session.delete(template)
            return True

    async def activate_template(self, template_id: int) -> bool:
        """Deactivate all templates, then activate the given one."""
        async with get_session() as session:
            # Deactivate all
            await session.execute(
                update(PostTemplate).values(is_active=False)
            )
            # Activate target
            stmt = select(PostTemplate).where(PostTemplate.id == template_id)
            result = await session.execute(stmt)
            template = result.scalars().first()
            if template is None:
                return False
            template.is_active = True
            await session.flush()
            return True

    # ────────────────────────────────────────────────────────
    #  SETTINGS
    # ────────────────────────────────────────────────────────

    async def get_setting(self, key: str) -> str | None:
        """Return the raw string value for a setting key, or None."""
        async with get_session() as session:
            stmt = select(Setting.value).where(Setting.key == key)
            result = await session.execute(stmt)
            return result.scalar()

    async def get_setting_int(self, key: str) -> int:
        """Return a setting value as int (defaults to 0)."""
        val = await self.get_setting(key)
        try:
            return int(val) if val else 0
        except (ValueError, TypeError):
            return 0

    async def get_setting_float(self, key: str) -> float:
        """Return a setting value as float (defaults to 0.0)."""
        val = await self.get_setting(key)
        try:
            return float(val) if val else 0.0
        except (ValueError, TypeError):
            return 0.0

    async def get_setting_bool(self, key: str) -> bool:
        """Return a setting value as bool (defaults to False)."""
        val = await self.get_setting(key)
        return val.lower() in ("true", "1", "yes") if val else False

    async def set_setting(self, key: str, value: str) -> None:
        """Create or update a single setting."""
        async with get_session() as session:
            stmt = select(Setting).where(Setting.key == key)
            result = await session.execute(stmt)
            setting = result.scalars().first()
            if setting is None:
                setting = Setting(key=key, value=value)
                session.add(setting)
            else:
                setting.value = value

    async def get_settings_by_group(self, group: str) -> list[Setting]:
        """Return all settings belonging to a group."""
        async with get_session() as session:
            stmt = (
                select(Setting)
                .where(Setting.group_name == group)
                .order_by(Setting.key)
            )
            result = await session.execute(stmt)
            return list(result.scalars().all())

    async def get_all_settings(self) -> list[Setting]:
        """Return every setting, ordered by group then key."""
        async with get_session() as session:
            stmt = select(Setting).order_by(Setting.group_name, Setting.key)
            result = await session.execute(stmt)
            return list(result.scalars().all())

    async def update_settings_bulk(
        self, settings_dict: dict[str, str]
    ) -> None:
        """Bulk-update settings from a ``{key: value}`` mapping."""
        async with get_session() as session:
            for key, value in settings_dict.items():
                stmt = select(Setting).where(Setting.key == key)
                result = await session.execute(stmt)
                setting = result.scalars().first()
                if setting is not None:
                    setting.value = str(value)
                else:
                    session.add(Setting(key=key, value=str(value)))

    # ────────────────────────────────────────────────────────
    #  SYSTEM LOGS
    # ────────────────────────────────────────────────────────

    async def log_event(
        self,
        level: str,
        component: str,
        action: str,
        message: str,
        details: str | None = None,
    ) -> None:
        """Write a structured log entry to the database."""
        try:
            async with get_session() as session:
                session.add(
                    SystemLog(
                        level=level,
                        component=component,
                        action=action,
                        message=message,
                        details=details,
                    )
                )
        except Exception as exc:
            logger.error("Failed to write system log: %s", exc)

    async def get_logs(
        self,
        limit: int = 100,
        offset: int = 0,
        level: str | None = None,
        component: str | None = None,
    ) -> list[SystemLog]:
        """Return system logs, newest first, with optional filters."""
        async with get_session() as session:
            stmt = select(SystemLog).order_by(SystemLog.created_at.desc())
            if level:
                stmt = stmt.where(SystemLog.level == level)
            if component:
                stmt = stmt.where(SystemLog.component == component)
            stmt = stmt.limit(limit).offset(offset)
            result = await session.execute(stmt)
            return list(result.scalars().all())

    async def get_logs_count(
        self,
        level: str | None = None,
        component: str | None = None,
    ) -> int:
        """Count of log entries, with optional filters."""
        async with get_session() as session:
            stmt = select(func.count()).select_from(SystemLog)
            if level:
                stmt = stmt.where(SystemLog.level == level)
            if component:
                stmt = stmt.where(SystemLog.component == component)
            result = await session.execute(stmt)
            return result.scalar() or 0

    # ────────────────────────────────────────────────────────
    #  DASHBOARD STATS
    # ────────────────────────────────────────────────────────

    async def get_dashboard_stats(self) -> dict[str, Any]:
        """Aggregate statistics for the dashboard overview."""
        now = datetime.utcnow()
        today_start = now.replace(hour=0, minute=0, second=0, microsecond=0)
        week_start = today_start - timedelta(days=7)

        async with get_session() as session:
            # Total published (status = "published")
            total_published = (
                await session.execute(
                    select(func.count())
                    .select_from(PublishedProduct)
                    .where(PublishedProduct.status == "published")
                )
            ).scalar() or 0

            # Today's count
            today_count = (
                await session.execute(
                    select(func.count())
                    .select_from(PublishedProduct)
                    .where(PublishedProduct.published_at >= today_start)
                )
            ).scalar() or 0

            # This week's count
            week_count = (
                await session.execute(
                    select(func.count())
                    .select_from(PublishedProduct)
                    .where(PublishedProduct.published_at >= week_start)
                )
            ).scalar() or 0

            # Failed count (all time)
            failed_count = (
                await session.execute(
                    select(func.count())
                    .select_from(PublishedProduct)
                    .where(PublishedProduct.status == "failed")
                )
            ).scalar() or 0

            # Success rate
            total_all = total_published + failed_count
            success_rate = (
                round((total_published / total_all) * 100, 1) if total_all > 0 else 100.0
            )

            # Active categories
            active_categories = (
                await session.execute(
                    select(func.count())
                    .select_from(Category)
                    .where(Category.is_active.is_(True))
                )
            ).scalar() or 0

            # Total keywords
            total_keywords = (
                await session.execute(
                    select(func.count()).select_from(Keyword)
                )
            ).scalar() or 0

            # Bot state from settings
            bot_running_val = (
                await session.execute(
                    select(Setting.value).where(Setting.key == "bot.is_running")
                )
            ).scalar()
            bot_is_running = (
                bot_running_val.lower() in ("true", "1", "yes")
                if bot_running_val
                else False
            )

            last_publish_at = (
                await session.execute(
                    select(Setting.value).where(
                        Setting.key == "bot.last_publish_at"
                    )
                )
            ).scalar() or None

        return {
            "total_published": total_published,
            "today_count": today_count,
            "week_count": week_count,
            "success_rate": success_rate,
            "failed_count": failed_count,
            "active_categories": active_categories,
            "total_keywords": total_keywords,
            "bot_is_running": bot_is_running,
            "last_publish_at": last_publish_at if last_publish_at else None,
        }
