"""End-of-run report: summary email + storage stats."""

from __future__ import annotations

import logging
import smtplib
from datetime import datetime
from email.mime.text import MIMEText
from typing import TYPE_CHECKING

if TYPE_CHECKING:
    from jobspy_v2.config.settings import Settings
    from jobspy_v2.storage.base import StorageBackend

logger = logging.getLogger(__name__)


def send_report(
    *,
    settings: Settings,
    storage: StorageBackend,
    mode: str,
    stats: dict,
    duration_seconds: float = 0.0,
) -> bool:
    """Send a summary report email and record run stats.

    Args:
        settings: Application settings.
        storage: Storage backend for persisting run stats.
        mode: Workflow mode ("onsite" or "remote").
        stats: Granular stats dict with keys matching RUN_STATS_COLUMNS.
        duration_seconds: Total pipeline duration.

    Returns:
        True if report email was sent successfully.
    """
    date_str = datetime.now().isoformat(timespec="seconds")
    boards = stats.get("boards_queried", [])
    boards_str = ", ".join(boards) if boards else "none"

    # ── Record run stats in storage ────────────────────────────────────
    stats_row = {
        "date": date_str,
        "mode": mode,
        "total_scraped": str(stats.get("total_scraped", 0)),
        "jobs_with_emails": str(stats.get("jobs_with_emails", 0)),
        "emails_sent": str(stats.get("emails_sent", 0)),
        "emails_failed": str(stats.get("emails_failed", 0)),
        "skipped_dedup_exact": str(stats.get("skipped_dedup_exact", 0)),
        "skipped_dedup_domain": str(stats.get("skipped_dedup_domain", 0)),
        "skipped_dedup_company": str(stats.get("skipped_dedup_company", 0)),
        "skipped_no_recipients": str(stats.get("skipped_no_recipients", 0)),
        "skipped_timeout": str(stats.get("skipped_timeout", 0)),
        "skipped_daily_quota": str(stats.get("skipped_daily_quota", 0)),
        "invalid_emails_filtered": str(stats.get("invalid_emails_filtered", 0)),
        "filtered_title": str(stats.get("filtered_title", 0)),
        "filtered_email": str(stats.get("filtered_email", 0)),
        "boards_queried": boards_str,
        "duration_seconds": f"{duration_seconds:.1f}",
        "dry_run": str(settings.dry_run),
        "run_stop_reason": stats.get("run_stop_reason", ""),
    }
    try:
        storage.add_run_stats(stats_row)
    except Exception:
        logger.exception("Failed to record run stats")

    # ── Log final summary ──────────────────────────────────────────────
    logger.info(
        "[%s] Run complete: scraped=%d, emails_sent=%d, emails_failed=%d, "
        "duration=%.1fmin, stop_reason=%s",
        mode,
        stats.get("total_scraped", 0),
        stats.get("emails_sent", 0),
        stats.get("emails_failed", 0),
        duration_seconds / 60,
        stats.get("run_stop_reason", "completed"),
    )

    # ── Build report email ─────────────────────────────────────────────
    emails_sent = int(stats.get("emails_sent", 0))
    subject = _build_subject(mode, emails_sent, settings.dry_run)
    body = _build_body(
        mode=mode,
        date_str=date_str,
        stats=stats,
        duration_seconds=duration_seconds,
        boards_str=boards_str,
        dry_run=settings.dry_run,
    )

    # ── Send report email ──────────────────────────────────────────────
    if not settings.report_email:
        logger.warning("No REPORT_EMAIL configured — skipping report email")
        return False

    return _send_report_email(
        to_email=settings.report_email,
        subject=subject,
        body=body,
        settings=settings,
    )


def _build_subject(mode: str, emails_sent: int, dry_run: bool) -> str:
    """Build report email subject line."""
    prefix = "[DRY RUN] " if dry_run else ""
    return f"{prefix}JobSpy-V2 Report: {mode.upper()} — {emails_sent} emails sent"


def _build_body(
    *,
    mode: str,
    date_str: str,
    stats: dict,
    duration_seconds: float,
    boards_str: str,
    dry_run: bool,
) -> str:
    """Build report email body with rich stats summary."""
    minutes = duration_seconds / 60
    status = "DRY RUN" if dry_run else "LIVE"

    total_scraped = stats.get("total_scraped", 0)
    jobs_with_emails = stats.get("jobs_with_emails", 0)
    emails_sent = stats.get("emails_sent", 0)
    emails_failed = stats.get("emails_failed", 0)
    skipped_exact = stats.get("skipped_dedup_exact", 0)
    skipped_company = stats.get("skipped_dedup_company", 0)
    skipped_no_recip = stats.get("skipped_no_recipients", 0)
    skipped_timeout = stats.get("skipped_timeout", 0)
    skipped_daily_quota = stats.get("skipped_daily_quota", 0)
    invalid_emails_filtered = stats.get("invalid_emails_filtered", 0)
    filtered_title = stats.get("filtered_title", 0)
    filtered_email = stats.get("filtered_email", 0)
    run_stop_reason = stats.get("run_stop_reason", "")
    pending_count = stats.get("pending_carried_over", 0)

    total_dedup = skipped_exact + skipped_company

    return f"""JobSpy-V2 Run Report
{"=" * 50}

Mode:            {mode.upper()} ({status})
Date:            {date_str}
Duration:        {minutes:.1f} minutes ({duration_seconds:.0f}s)
Boards queried:  {boards_str}
Stop reason:     {run_stop_reason or "completed"}

Scraping Summary
{"-" * 50}
Total scraped:             {total_scraped}
Filtered (no email):       {filtered_email}
Filtered (title):          {filtered_title}
Jobs with emails:          {jobs_with_emails}

Email Summary
{"-" * 50}
Emails sent:               {emails_sent}
Emails failed:             {emails_failed}
Success rate:              {_calc_rate(emails_sent, emails_sent + emails_failed)}

Skipped — Reasons
{"-" * 50}
No valid recipients:       {skipped_no_recip}
Invalid emails (DNS):      {invalid_emails_filtered}
Timed out (not processed): {skipped_timeout}
Daily quota hit:           {skipped_daily_quota}

Pending Jobs
{"-" * 50}
Pending carry-over:        {pending_count}

Dedup Breakdown
{"-" * 50}
Exact email match:         {skipped_exact}
Company cooldown:          {skipped_company}
Total dedup skipped:       {total_dedup}
"""


def _calc_rate(success: int, total: int) -> str:
    """Calculate success rate as percentage string."""
    if total == 0:
        return "N/A"
    return f"{(success / total) * 100:.1f}%"


def _send_report_email(
    *,
    to_email: str,
    subject: str,
    body: str,
    settings: Settings,
) -> bool:
    """Send report email via SMTP (no retry — best effort)."""
    try:
        msg = MIMEText(body, "plain", "utf-8")
        msg["From"] = f"JobSpy-V2 <{settings.gmail_email}>"
        msg["To"] = to_email
        msg["Subject"] = subject

        with smtplib.SMTP(settings.smtp_host, settings.smtp_port, timeout=30) as server:
            server.ehlo()
            server.starttls()
            server.ehlo()
            server.login(settings.gmail_email, settings.gmail_app_password)
            server.send_message(msg)

        logger.info("Report email sent to %s", to_email)
        return True
    except Exception:
        logger.exception("Failed to send report email")
        return False
