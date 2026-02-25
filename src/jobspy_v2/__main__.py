"""CLI entry point — ``python -m jobspy_v2 onsite|remote|serve [--dry-run]``."""

from __future__ import annotations

import argparse
import logging
import sys
import warnings

# jobspy calls job.dict() internally which triggers PydanticDeprecatedSince20.
# The warning fires from jobspy's worker threads, so module="pydantic" doesn't
# match (that filters by caller module, not by warning origin). Use message= instead.
warnings.filterwarnings(
    "ignore",
    message=".*`dict` method is deprecated.*",
    category=DeprecationWarning,
)
warnings.filterwarnings(
    "ignore",
    message=".*use `model_dump` instead.*",
    category=DeprecationWarning,
)

from jobspy_v2.config import get_settings  # noqa: E402


def _build_parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(
        prog="jobspy_v2",
        description="JobSpy-V2 — automated job scraping & cold email outreach.",
    )
    sub = parser.add_subparsers(dest="command", required=True)

    # Shared flags
    for name, help_text in [
        ("onsite", "Run onsite/hybrid job pipeline once."),
        ("remote", "Run remote job pipeline once."),
        ("serve", "Start long-lived scheduler (cron + health check)."),
    ]:
        p = sub.add_parser(name, help=help_text)
        p.add_argument(
            "--dry-run",
            action="store_true",
            default=False,
            help="Simulate emails without sending.",
        )

    return parser


def main(argv: list[str] | None = None) -> int:
    """Parse CLI args and dispatch to the correct workflow or serve mode."""
    parser = _build_parser()
    args = parser.parse_args(argv)

    settings = get_settings()

    # Override dry_run from CLI flag
    if args.dry_run:
        settings = settings.model_copy(update={"dry_run": True})

    # Configure logging
    logging.basicConfig(
        level=getattr(logging, settings.log_level.upper(), logging.INFO),
        format="%(asctime)s [%(levelname)s] %(name)s: %(message)s",
        datefmt="%Y-%m-%d %H:%M:%S",
    )

    if args.command == "onsite":
        from jobspy_v2.workflows.onsite import OnsiteWorkflow

        return OnsiteWorkflow(settings).run()

    if args.command == "remote":
        from jobspy_v2.workflows.remote import RemoteWorkflow

        return RemoteWorkflow(settings).run()

    if args.command == "serve":
        from jobspy_v2.scheduler.runner import serve

        serve(settings)
        return 0

    return 1  # unreachable with required=True


if __name__ == "__main__":
    sys.exit(main())
