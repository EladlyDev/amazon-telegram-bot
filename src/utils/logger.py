"""Loguru-based logging configuration."""

from __future__ import annotations

import sys

from loguru import logger


def setup_logger(
    log_level: str = "INFO",
    log_file: str = "logs/bot.log",
    rotation: str = "10 MB",
    retention: str = "30 days",
) -> logger.__class__:  # type: ignore[name-defined]
    """Configure and return a loguru logger instance.

    * Removes the default stderr handler.
    * Adds a coloured stderr handler at the specified level.
    * Adds a rotating file handler with UTF-8 encoding.

    Args:
        log_level: Minimum severity to emit (e.g. ``"DEBUG"``, ``"INFO"``).
        log_file: Path to the log file.
        rotation: When to rotate (e.g. ``"10 MB"``, ``"1 day"``).
        retention: How long to keep old log files (e.g. ``"30 days"``).

    Returns:
        The configured ``loguru.logger`` singleton.
    """
    # Remove all existing handlers (including the default one)
    logger.remove()

    # Stderr handler — coloured, human-readable
    logger.add(
        sys.stderr,
        level=log_level,
        format=(
            "<green>{time:YYYY-MM-DD HH:mm:ss}</green> | "
            "<level>{level: <8}</level> | "
            "<cyan>{name}</cyan>:<cyan>{function}</cyan>:<cyan>{line}</cyan> - "
            "<level>{message}</level>"
        ),
        colorize=True,
    )

    # File handler — machine-readable, rotated, UTF-8
    logger.add(
        log_file,
        level=log_level,
        format=(
            "{time:YYYY-MM-DD HH:mm:ss} | "
            "{level: <8} | "
            "{name}:{function}:{line} - "
            "{message}"
        ),
        rotation=rotation,
        retention=retention,
        encoding="utf-8",
    )

    logger.info("Logger initialised — level={}, file={}", log_level, log_file)
    return logger
