"""Async database connection, session management, and initialization.

Provides the SQLAlchemy async engine, session factory, table creation,
and seeding of default settings / templates / schedule.
"""

import logging
from contextlib import asynccontextmanager
from collections.abc import AsyncGenerator
from pathlib import Path

from sqlalchemy.ext.asyncio import AsyncSession, async_sessionmaker, create_async_engine
from sqlalchemy import select

from src.config import settings
from src.database.models import (
    Base,
    PostTemplate,
    Schedule,
    Setting,
    DEFAULT_SETTINGS,
    DEFAULT_TEMPLATES,
)

logger = logging.getLogger(__name__)

# ── Ensure data directory exists ────────────────────────────
Path(settings.database_path).parent.mkdir(parents=True, exist_ok=True)

# ── Engine & session factory ────────────────────────────────
engine = create_async_engine(
    settings.database_url,
    echo=False,
    pool_pre_ping=True,
)

async_session = async_sessionmaker(
    engine,
    class_=AsyncSession,
    expire_on_commit=False,
)


# ── Session context manager ────────────────────────────────


@asynccontextmanager
async def get_session() -> AsyncGenerator[AsyncSession, None]:
    """Yield an async session and handle commit / rollback lifecycle."""
    session = async_session()
    try:
        yield session
        await session.commit()
    except Exception:
        await session.rollback()
        raise
    finally:
        await session.close()


# ── Initialization ──────────────────────────────────────────


async def init_db() -> None:
    """Create all tables and seed default data."""
    async with engine.begin() as conn:
        await conn.run_sync(Base.metadata.create_all)
    logger.info("Database tables created.")
    await seed_defaults()


async def seed_defaults() -> None:
    """Insert default settings, templates, and schedule if they don't exist."""
    async with get_session() as session:
        # ── Settings ────────────────────────────────────────
        existing_keys = set(
            (await session.execute(select(Setting.key))).scalars().all()
        )
        new_settings = [
            Setting(**s) for s in DEFAULT_SETTINGS if s["key"] not in existing_keys
        ]
        if new_settings:
            session.add_all(new_settings)
            logger.info("Seeded %d default settings.", len(new_settings))

        # ── Templates ───────────────────────────────────────
        template_count = (
            await session.execute(select(PostTemplate.id))
        ).scalars().first()
        if template_count is None:
            for t in DEFAULT_TEMPLATES:
                session.add(PostTemplate(**t))
            logger.info("Seeded %d default templates.", len(DEFAULT_TEMPLATES))

        # ── Schedule ────────────────────────────────────────
        schedule_exists = (
            await session.execute(select(Schedule.id))
        ).scalars().first()
        if schedule_exists is None:
            session.add(Schedule())
            logger.info("Seeded default schedule.")
