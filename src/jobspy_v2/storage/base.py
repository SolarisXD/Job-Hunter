"""Storage backend protocol — defines the contract all backends must implement."""

from __future__ import annotations

from typing import Protocol

# Column definitions for each data table
SENT_EMAIL_COLUMNS: tuple[str, ...] = (
    "email",
    "domain",
    "company",
    "date_sent",
    "job_title",
    "job_url",
    "location",
    "is_remote",
    "subject",
    "body_preview",
    "mode",
    "word_count",
)

SCRAPED_JOB_COLUMNS: tuple[str, ...] = (
    "date_scraped",
    "board",
    "title",
    "company",
    "company_url",
    "location",
    "is_remote",
    "job_url",
    "job_type",
    "date_posted",
    "emails",
    "salary_min",
    "salary_max",
    "salary_currency",
    "salary_interval",
    "skills",
    "experience_range",
    "job_level",
    "company_industry",
    "email_sent",
    "skip_reason",
    "email_recipient",
    "row_number",  # Track row number for carry-over processing
)

RUN_STATS_COLUMNS: tuple[str, ...] = (
    "date",
    "mode",
    "total_scraped",
    "jobs_with_emails",
    "emails_sent",
    "emails_failed",
    "skipped_dedup_exact",
    "skipped_dedup_domain",
    "skipped_dedup_company",
    "skipped_no_recipients",
    "skipped_timeout",
    "skipped_daily_quota",
    "invalid_emails_filtered",
    "filtered_title",
    "filtered_email",
    "boards_queried",
    "duration_seconds",
    "dry_run",
    "run_stop_reason",
)


class StorageBackend(Protocol):
    """Protocol for storage backends (Google Sheets, CSV, etc.)."""

    def get_sent_emails(self) -> list[dict[str, str]]:
        """Return all previously sent email records."""
        ...

    def add_sent_email(self, record: dict[str, str]) -> None:
        """Append a single sent email record."""
        ...

    def add_scraped_jobs(self, records: list[dict[str, str]]) -> int:
        """Append a batch of scraped job records.

        Returns the starting row number (1-indexed, after header) in the
        worksheet so callers can update individual rows later.
        """
        ...

    def update_scraped_job_status(
        self,
        row_number: int,
        email_sent: str,
        skip_reason: str,
        email_recipient: str,
    ) -> None:
        """Update email_sent, skip_reason, and email_recipient for a scraped job row."""
        ...

    def get_pending_jobs(self) -> list[dict[str, str]]:
        """Return all jobs with email_sent='Pending' status.

        Used for carry-over: jobs not processed due to daily limit in previous runs.
        """
        ...

    def get_run_stats(self) -> list[dict[str, str]]:
        """Return all run statistics records."""
        ...

    def add_run_stats(self, stats: dict[str, str]) -> None:
        """Append a single run statistics record."""
        ...

    def get_today_sent_emails_count(self) -> int:
        """Return the count of emails sent today (both remote and onsite)."""
        ...
