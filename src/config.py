"""Application configuration using pydantic-settings.

Loads settings from .env file with typed fields, defaults, and validation.
"""

from pathlib import Path
from functools import cached_property

from pydantic import model_validator
from pydantic_settings import BaseSettings, SettingsConfigDict


class Settings(BaseSettings):
    """Central configuration for the Amazon-Telegram bot."""

    model_config = SettingsConfigDict(
        env_file=".env",
        env_file_encoding="utf-8",
        extra="ignore",
    )

    # ── Amazon ──────────────────────────────────────────────
    amazon_access_key: str = ""
    amazon_secret_key: str = ""
    amazon_partner_tag: str = ""
    amazon_marketplace: str = "www.amazon.sa"
    amazon_region: str = "eu-west-1"

    # ── Telegram ────────────────────────────────────────────
    telegram_bot_token: str = ""
    telegram_channel_id: str = ""
    telegram_admin_chat_id: str = ""

    # ── Dashboard ───────────────────────────────────────────
    dashboard_username: str = "admin"
    dashboard_password: str = "admin123"
    dashboard_secret_key: str = "change-me-in-production"
    dashboard_port: int = 8000

    # ── Database ────────────────────────────────────────────
    database_path: str = "data/bot.db"

    # ── Logging ─────────────────────────────────────────────
    log_level: str = "INFO"

    # ── Environment ────────────────────────────────────────
    environment: str = "development"  # "development" or "production"

    # ── Validators ──────────────────────────────────────────

    @model_validator(mode="after")
    def _ensure_database_dir(self) -> "Settings":
        """Create the parent directory for the database file if it doesn't exist."""
        db_dir = Path(self.database_path).parent
        db_dir.mkdir(parents=True, exist_ok=True)
        return self

    # ── Computed properties ─────────────────────────────────

    @cached_property
    def database_url(self) -> str:
        """SQLAlchemy async connection string for the SQLite database."""
        return f"sqlite+aiosqlite:///{self.database_path}"


# ── Singleton instance ──────────────────────────────────────
settings = Settings()
