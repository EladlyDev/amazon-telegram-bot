"""Async SQLite database layer for product tracking and publication history."""

from __future__ import annotations

from datetime import datetime, timedelta
from typing import Optional

import aiosqlite
from loguru import logger


class Database:
    """Async wrapper around an SQLite database for the bot.

    Manages three tables:
    * **products** — every product the bot has seen.
    * **published** — log of every message sent to a Telegram channel.
    * **config** — key/value runtime configuration store.

    Args:
        db_path: Path to the SQLite file (created automatically).
    """

    def __init__(self, db_path: str = "data/bot.db") -> None:
        self.db_path = db_path
        self._db: Optional[aiosqlite.Connection] = None

    # ------------------------------------------------------------------
    # Lifecycle
    # ------------------------------------------------------------------

    async def initialize(self) -> None:
        """Open the connection and ensure all tables / indexes exist."""
        try:
            self._db = await aiosqlite.connect(self.db_path)
            self._db.row_factory = aiosqlite.Row

            await self._db.executescript(
                """
                CREATE TABLE IF NOT EXISTS products (
                    asin            TEXT PRIMARY KEY,
                    title           TEXT NOT NULL,
                    current_price   REAL,
                    original_price  REAL,
                    category        TEXT,
                    first_seen      TIMESTAMP DEFAULT CURRENT_TIMESTAMP,
                    last_seen       TIMESTAMP DEFAULT CURRENT_TIMESTAMP
                );

                CREATE TABLE IF NOT EXISTS published (
                    id              INTEGER PRIMARY KEY AUTOINCREMENT,
                    asin            TEXT NOT NULL,
                    channel_id      TEXT NOT NULL,
                    message_id      INTEGER,
                    published_at    TIMESTAMP DEFAULT CURRENT_TIMESTAMP,
                    template_used   TEXT
                );

                CREATE TABLE IF NOT EXISTS config (
                    key             TEXT PRIMARY KEY,
                    value           TEXT NOT NULL,
                    updated_at      TIMESTAMP DEFAULT CURRENT_TIMESTAMP
                );

                CREATE INDEX IF NOT EXISTS idx_published_asin
                    ON published(asin);
                CREATE INDEX IF NOT EXISTS idx_published_date
                    ON published(published_at);
                """
            )
            await self._db.commit()
            logger.info("Database initialized at {}", self.db_path)
        except Exception:
            logger.exception("Failed to initialize database")
            raise

    async def close(self) -> None:
        """Close the database connection gracefully."""
        if self._db:
            try:
                await self._db.close()
                logger.debug("Database connection closed")
            except Exception:
                logger.exception("Error closing database connection")

    # ------------------------------------------------------------------
    # Products
    # ------------------------------------------------------------------

    async def record_product(
        self,
        asin: str,
        title: str,
        price: Optional[float],
        original_price: Optional[float],
        category: Optional[str],
    ) -> None:
        """Insert a product or update its prices and ``last_seen`` timestamp.

        Uses an ``INSERT … ON CONFLICT`` (upsert) so the same ASIN is
        never duplicated.
        """
        try:
            await self._db.execute(  # type: ignore[union-attr]
                """
                INSERT INTO products (asin, title, current_price,
                                      original_price, category)
                VALUES (?, ?, ?, ?, ?)
                ON CONFLICT(asin) DO UPDATE SET
                    title          = excluded.title,
                    current_price  = excluded.current_price,
                    original_price = excluded.original_price,
                    category       = excluded.category,
                    last_seen      = CURRENT_TIMESTAMP
                """,
                (asin, title, price, original_price, category),
            )
            await self._db.commit()  # type: ignore[union-attr]
            logger.debug("Recorded product {}", asin)
        except Exception:
            logger.exception("Failed to record product {}", asin)

    # ------------------------------------------------------------------
    # Publishing
    # ------------------------------------------------------------------

    async def is_recently_published(
        self, asin: str, cooldown_days: int = 7
    ) -> bool:
        """Return ``True`` if *asin* was published within *cooldown_days*."""
        try:
            cutoff = datetime.utcnow() - timedelta(days=cooldown_days)
            cursor = await self._db.execute(  # type: ignore[union-attr]
                """
                SELECT COUNT(*) AS cnt
                FROM published
                WHERE asin = ? AND published_at >= ?
                """,
                (asin, cutoff.isoformat()),
            )
            row = await cursor.fetchone()
            return (row[0] if row else 0) > 0
        except Exception:
            logger.exception("Error checking recent publication for {}", asin)
            return False

    async def record_publication(
        self,
        asin: str,
        channel_id: str,
        message_id: Optional[int],
        template: str = "default",
    ) -> None:
        """Log a published message to the ``published`` table."""
        try:
            await self._db.execute(  # type: ignore[union-attr]
                """
                INSERT INTO published (asin, channel_id, message_id,
                                       template_used)
                VALUES (?, ?, ?, ?)
                """,
                (asin, channel_id, message_id, template),
            )
            await self._db.commit()  # type: ignore[union-attr]
            logger.debug("Recorded publication for {} in {}", asin, channel_id)
        except Exception:
            logger.exception("Failed to record publication for {}", asin)

    async def get_published_history(
        self, limit: int = 50
    ) -> list[dict]:
        """Return the most recent published records as a list of dicts.

        Results are ordered by ``published_at DESC``.
        """
        try:
            cursor = await self._db.execute(  # type: ignore[union-attr]
                """
                SELECT p.asin, p.channel_id, p.message_id,
                       p.published_at, p.template_used,
                       pr.title, pr.current_price, pr.category
                FROM published p
                LEFT JOIN products pr ON p.asin = pr.asin
                ORDER BY p.published_at DESC
                LIMIT ?
                """,
                (limit,),
            )
            rows = await cursor.fetchall()
            return [dict(row) for row in rows]
        except Exception:
            logger.exception("Failed to fetch published history")
            return []

    # ------------------------------------------------------------------
    # Stats
    # ------------------------------------------------------------------

    async def get_publish_stats(self, days: int = 7) -> dict:
        """Return summary statistics for the last *days* days.

        Returns:
            A dict with keys ``total_posts``, ``unique_products``, and
            ``period_days``.
        """
        try:
            cutoff = datetime.utcnow() - timedelta(days=days)
            cursor = await self._db.execute(  # type: ignore[union-attr]
                """
                SELECT COUNT(*) AS total_posts,
                       COUNT(DISTINCT asin) AS unique_products
                FROM published
                WHERE published_at >= ?
                """,
                (cutoff.isoformat(),),
            )
            row = await cursor.fetchone()
            return {
                "total_posts": row[0] if row else 0,
                "unique_products": row[1] if row else 0,
                "period_days": days,
            }
        except Exception:
            logger.exception("Failed to fetch publish stats")
            return {"total_posts": 0, "unique_products": 0, "period_days": days}

    # ------------------------------------------------------------------
    # Config KV store
    # ------------------------------------------------------------------

    async def get_config(
        self, key: str, default: Optional[str] = None
    ) -> Optional[str]:
        """Retrieve a runtime configuration value by key."""
        try:
            cursor = await self._db.execute(  # type: ignore[union-attr]
                "SELECT value FROM config WHERE key = ?", (key,)
            )
            row = await cursor.fetchone()
            return row[0] if row else default
        except Exception:
            logger.exception("Failed to get config key '{}'", key)
            return default

    async def set_config(self, key: str, value: str) -> None:
        """Set a runtime configuration value (upsert)."""
        try:
            await self._db.execute(  # type: ignore[union-attr]
                """
                INSERT INTO config (key, value)
                VALUES (?, ?)
                ON CONFLICT(key) DO UPDATE SET
                    value      = excluded.value,
                    updated_at = CURRENT_TIMESTAMP
                """,
                (key, value),
            )
            await self._db.commit()  # type: ignore[union-attr]
            logger.debug("Config '{}' set to '{}'", key, value)
        except Exception:
            logger.exception("Failed to set config key '{}'", key)
