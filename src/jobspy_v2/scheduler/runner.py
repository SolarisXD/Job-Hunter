"""Serve-mode orchestrator — runs scheduled workflows with health check."""

from __future__ import annotations

import logging
import signal
import threading
import time
from typing import TYPE_CHECKING

from jobspy_v2.scheduler.cron_scheduler import CronScheduler
from jobspy_v2.scheduler.health_check import start_health_check

if TYPE_CHECKING:
    from jobspy_v2.config.settings import Settings

logger = logging.getLogger(__name__)


def _run_workflow(workflow_cls: type, settings: Settings) -> None:
    """Instantiate and run a workflow. Used as cron job target."""
    name = workflow_cls.__name__
    logger.info("Cron trigger: starting %s", name)
    try:
        workflow = workflow_cls(settings)
        exit_code = workflow.run()
        logger.info("%s completed with exit code %d", name, exit_code)
    except Exception:
        logger.exception("Fatal error in %s", name)


def serve(settings: Settings) -> None:
    """Start the scheduler with onsite+remote cron jobs and block forever.

    This is the entry point for ``python -m jobspy_v2 serve``.
    Starts a health-check HTTP server for PaaS keep-alive, schedules both
    onsite and remote workflows on their configured cron expressions, and
    blocks until SIGINT/SIGTERM.
    """
    # Lazy import to avoid circular deps
    from jobspy_v2.workflows.onsite import OnsiteWorkflow
    from jobspy_v2.workflows.remote import RemoteWorkflow

    scheduler = CronScheduler()
    scheduler.add_job(
        _run_workflow,
        cron_expr=settings.scheduler_onsite_cron,
        job_id="onsite",
        workflow_cls=OnsiteWorkflow,
        settings=settings,
    )
    scheduler.add_job(
        _run_workflow,
        cron_expr=settings.scheduler_remote_cron,
        job_id="remote",
        workflow_cls=RemoteWorkflow,
        settings=settings,
    )

    # Health check for PaaS
    server = None
    if settings.health_check_enabled:
        server, _ = start_health_check(
            port=settings.health_check_port,
            path=settings.health_check_path,
        )

    scheduler.start()
    logger.info(
        "Serve mode active — onsite='%s', remote='%s'",
        settings.scheduler_onsite_cron,
        settings.scheduler_remote_cron,
    )

    # Block until signal
    stop_event = threading.Event()

    def _handle_signal(signum: int, frame: object) -> None:
        logger.info("Received signal %d, shutting down...", signum)
        stop_event.set()

    signal.signal(signal.SIGINT, _handle_signal)
    signal.signal(signal.SIGTERM, _handle_signal)

    try:
        while not stop_event.is_set():
            time.sleep(1)
    finally:
        scheduler.shutdown()
        if server:
            server.shutdown()
        logger.info("Serve mode stopped")
