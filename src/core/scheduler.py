"""APScheduler wrapper for timed publish cycles."""

from __future__ import annotations

from typing import Callable, Optional

from apscheduler.schedulers.asyncio import AsyncIOScheduler
from apscheduler.triggers.cron import CronTrigger
from apscheduler.triggers.interval import IntervalTrigger
from loguru import logger


class BotScheduler:
    """Manages scheduled publishing cycles using APScheduler.

    Reads ``schedule`` settings from the config dict and creates
    cron or interval jobs that fire an async callback.

    Args:
        config: The full ``settings.yaml`` content as a dict.
    """

    def __init__(self, config: dict) -> None:
        self._config = config
        self._scheduler = AsyncIOScheduler(timezone="UTC")
        self._callback: Optional[Callable] = None

    # ------------------------------------------------------------------
    # Public API
    # ------------------------------------------------------------------

    def set_callback(self, callback: Callable) -> None:
        """Register the async function to invoke on each scheduled run.

        Args:
            callback: Typically ``BotOrchestrator.run_single_cycle``.
        """
        self._callback = callback

    def setup_jobs(self) -> None:
        """Create scheduler jobs based on the loaded config.

        Supports two modes:
        * ``fixed_times`` — a list of ``"HH:MM"`` strings → cron jobs.
        * ``interval_minutes`` — a single int → interval job.
        """
        schedule_cfg: dict = self._config.get("schedule", {})

        fixed_times: Optional[list[str]] = schedule_cfg.get("fixed_times")

        if fixed_times:
            for time_str in fixed_times:
                try:
                    hour, minute = time_str.split(":")
                    self._scheduler.add_job(
                        self._callback,
                        CronTrigger(hour=int(hour), minute=int(minute)),
                        id=f"publish_{time_str}",
                        replace_existing=True,
                    )
                    logger.info("Scheduled publish job at {} UTC", time_str)
                except (ValueError, TypeError):
                    logger.error("Invalid time format: '{}'", time_str)
            return

        interval: Optional[int] = schedule_cfg.get("interval_minutes")
        if interval:
            self._scheduler.add_job(
                self._callback,
                IntervalTrigger(minutes=interval),
                id="publish_interval",
                replace_existing=True,
            )
            logger.info("Scheduled publish job every {} minutes", interval)

    def start(self) -> None:
        """Start the scheduler."""
        self._scheduler.start()
        logger.info("Scheduler started")

    def stop(self) -> None:
        """Shut down the scheduler gracefully."""
        try:
            self._scheduler.shutdown(wait=False)
            logger.info("Scheduler stopped")
        except Exception:
            logger.exception("Error stopping scheduler")

    def update_schedule(self, new_times: list[str]) -> None:
        """Replace the current schedule with new fixed times.

        Args:
            new_times: List of ``"HH:MM"`` strings.
        """
        # Remove all existing jobs
        for job in self._scheduler.get_jobs():
            job.remove()

        # Update internal config
        if "schedule" not in self._config:
            self._config["schedule"] = {}
        self._config["schedule"]["fixed_times"] = new_times

        # Re-create jobs
        self.setup_jobs()
        logger.info("Schedule updated dynamically to {}", new_times)

    def get_next_run_times(self, count: int = 5) -> list[str]:
        """Return the next *count* scheduled run times as ISO strings.

        Args:
            count: Maximum number of upcoming times to return.

        Returns:
            List of ISO-formatted datetime strings.
        """
        times: list[str] = []
        for job in self._scheduler.get_jobs():
            if job.next_run_time is not None:
                times.append(job.next_run_time.isoformat())
        # Sort and limit
        times.sort()
        return times[:count]
