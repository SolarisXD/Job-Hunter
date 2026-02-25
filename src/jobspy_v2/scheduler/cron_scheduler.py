"""Thin wrapper around APScheduler v3 for cron-based job scheduling."""

from __future__ import annotations

import logging
from typing import Any, Callable

from apscheduler.schedulers.background import BackgroundScheduler
from apscheduler.triggers.cron import CronTrigger

logger = logging.getLogger(__name__)

_CRON_FIELDS = ("minute", "hour", "day", "month", "day_of_week")


def parse_cron(cron_expr: str) -> dict[str, str]:
    """Parse a 5-field cron expression into a dict for CronTrigger.

    Format: "minute hour day month day_of_week"
    Example: "30 2 * * *" → {"minute": "30", "hour": "2", ...}

    Raises:
        ValueError: If expression doesn't have exactly 5 fields.
    """
    parts = cron_expr.strip().split()
    if len(parts) != 5:
        msg = f"Cron expression must have 5 fields, got {len(parts)}: '{cron_expr}'"
        raise ValueError(msg)
    return dict(zip(_CRON_FIELDS, parts))


class CronScheduler:
    """Manages cron-triggered background jobs via APScheduler."""

    def __init__(self) -> None:
        self._scheduler = BackgroundScheduler(timezone="UTC")

    def add_job(
        self,
        func: Callable[..., Any],
        cron_expr: str,
        job_id: str,
        **kwargs: Any,
    ) -> None:
        """Schedule a function to run on a cron schedule.

        Args:
            func: Callable to execute.
            cron_expr: 5-field cron expression (e.g. "30 2 * * *").
            job_id: Unique identifier for this job.
            **kwargs: Additional keyword arguments passed to func.
        """
        cron_fields = parse_cron(cron_expr)
        trigger = CronTrigger(**cron_fields)
        self._scheduler.add_job(func, trigger, id=job_id, kwargs=kwargs)
        logger.info("Scheduled job '%s' with cron '%s'", job_id, cron_expr)

    def start(self) -> None:
        """Start the scheduler."""
        self._scheduler.start()
        logger.info("Scheduler started")

    def shutdown(self, wait: bool = True) -> None:
        """Shut down the scheduler."""
        self._scheduler.shutdown(wait=wait)
        logger.info("Scheduler shut down")

    @property
    def running(self) -> bool:
        """Whether the scheduler is currently running."""
        return self._scheduler.running
