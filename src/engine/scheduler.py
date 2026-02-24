"""APScheduler wrapper for the publishing schedule.

Reads schedule configuration from the database and creates
APScheduler jobs accordingly. Supports two modes:

- **fixed_times**: Publish at specific clock times each day.
- **interval**: Publish every *N* minutes.
"""

from __future__ import annotations

import json
import logging
from datetime import datetime

from apscheduler.schedulers.asyncio import AsyncIOScheduler
from apscheduler.triggers.cron import CronTrigger
from apscheduler.triggers.interval import IntervalTrigger

from src.database.repository import Repository

logger = logging.getLogger(__name__)


class PublishScheduler:
    """Manages APScheduler jobs based on the DB schedule configuration."""

    JOB_ID_PREFIX = "publish_"

    def __init__(self, engine, repository: Repository) -> None:
        # Import here to avoid circular imports at module level
        from src.engine.core import BotEngine

        self.engine: BotEngine = engine
        self.repo = repository
        self.scheduler = AsyncIOScheduler()
        self._started: bool = False

    # ────────────────────────────────────────────────────────
    #  Lifecycle
    # ────────────────────────────────────────────────────────

    async def start(self) -> None:
        """Load the schedule from the DB and start the scheduler."""
        await self.reload_schedule()
        if not self._started:
            self.scheduler.start()
            self._started = True
            logger.info("Scheduler started.")

    def stop(self) -> None:
        """Stop the scheduler gracefully."""
        if self._started:
            self.scheduler.shutdown(wait=False)
            self._started = False
            logger.info("Scheduler stopped.")

    @property
    def is_running(self) -> bool:
        """``True`` if the scheduler is actively running."""
        return self._started and self.scheduler.running

    # ────────────────────────────────────────────────────────
    #  Schedule management
    # ────────────────────────────────────────────────────────

    async def reload_schedule(self) -> None:
        """Reload schedule configuration from the database.

        Removes all existing publish jobs and recreates them
        based on the active schedule record.
        """
        # 1. Remove existing publish jobs
        for job in self.scheduler.get_jobs():
            if job.id.startswith(self.JOB_ID_PREFIX):
                job.remove()
        logger.debug("Cleared existing publish jobs.")

        # 2. Get active schedule
        schedule = await self.repo.get_active_schedule()
        if schedule is None or not schedule.is_active:
            logger.info("No active schedule — no jobs created.")
            return

        tz = schedule.timezone or "Asia/Riyadh"

        # 3. Create jobs based on schedule type
        if schedule.schedule_type == "fixed_times":
            await self._create_fixed_time_jobs(schedule.fixed_times, tz)
        elif schedule.schedule_type == "interval":
            self._create_interval_job(schedule.interval_minutes, tz)
        else:
            logger.warning(
                "Unknown schedule_type '%s' — no jobs created.",
                schedule.schedule_type,
            )

    async def _create_fixed_time_jobs(
        self, fixed_times_json: str, tz: str
    ) -> None:
        """Parse the JSON array of ``"HH:MM"`` strings and create cron jobs."""
        try:
            times: list[str] = json.loads(fixed_times_json)
        except (json.JSONDecodeError, TypeError) as exc:
            logger.error("Invalid fixed_times JSON: %s", exc)
            return

        for i, time_str in enumerate(times):
            try:
                parts = time_str.strip().split(":")
                hour, minute = int(parts[0]), int(parts[1])
            except (ValueError, IndexError):
                logger.warning("Skipping invalid time entry: '%s'", time_str)
                continue

            job_id = f"{self.JOB_ID_PREFIX}fixed_{i}"
            self.scheduler.add_job(
                self.engine.run_publish_cycle,
                trigger=CronTrigger(hour=hour, minute=minute, timezone=tz),
                id=job_id,
                replace_existing=True,
                name=f"Publish at {hour:02d}:{minute:02d}",
            )
            logger.info(
                "Scheduled job '%s' at %02d:%02d (%s).", job_id, hour, minute, tz
            )

    def _create_interval_job(self, interval_minutes: int, tz: str) -> None:
        """Create an interval-based publishing job."""
        if interval_minutes <= 0:
            logger.warning("Invalid interval_minutes=%d — skipping.", interval_minutes)
            return

        job_id = f"{self.JOB_ID_PREFIX}interval"
        self.scheduler.add_job(
            self.engine.run_publish_cycle,
            trigger=IntervalTrigger(minutes=interval_minutes, timezone=tz),
            id=job_id,
            replace_existing=True,
            name=f"Publish every {interval_minutes} min",
        )
        logger.info(
            "Scheduled job '%s' every %d minutes (%s).",
            job_id, interval_minutes, tz,
        )

    # ────────────────────────────────────────────────────────
    #  Info helpers
    # ────────────────────────────────────────────────────────

    def get_next_run_time(self) -> str | None:
        """Get the soonest next run time formatted as ``"HH:MM"``.

        Returns ``None`` if no jobs are scheduled.
        """
        jobs = [
            j for j in self.scheduler.get_jobs()
            if j.id.startswith(self.JOB_ID_PREFIX)
        ]
        if not jobs:
            return None

        next_times = [j.next_run_time for j in jobs if j.next_run_time]
        if not next_times:
            return None

        soonest: datetime = min(next_times)
        return soonest.strftime("%H:%M")

    def get_scheduled_jobs_info(self) -> list[dict]:
        """Return info about all active publish jobs.

        Returns:
            List of ``{"id": str, "next_run": str, "trigger": str}``.
        """
        result: list[dict] = []
        for job in self.scheduler.get_jobs():
            if not job.id.startswith(self.JOB_ID_PREFIX):
                continue
            result.append({
                "id": job.id,
                "next_run": (
                    job.next_run_time.strftime("%Y-%m-%d %H:%M:%S")
                    if job.next_run_time
                    else "—"
                ),
                "trigger": str(job.trigger),
            })
        return result
