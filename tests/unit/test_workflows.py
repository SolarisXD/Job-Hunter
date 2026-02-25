"""Tests for workflows (base, onsite, remote) and CLI (__main__)."""

from __future__ import annotations

from dataclasses import dataclass
from unittest.mock import MagicMock, patch

import pandas as pd
import pytest

from jobspy_v2.config import Settings, get_settings

# ---------------------------------------------------------------------------
# Helpers
# ---------------------------------------------------------------------------

MODULE = "jobspy_v2.workflows.base"


def _make_settings(**overrides: object) -> Settings:
    """Create a Settings instance with test defaults (no .env leakage)."""
    get_settings.cache_clear()
    defaults: dict = {
        "gmail_email": "test@gmail.com",
        "gmail_app_password": "pass",
        "openrouter_api_key": "sk-test",
        "contact_name": "Test",
        "contact_email": "test@test.com",
        "contact_phone": "+1234567890",
        "contact_portfolio": "test.dev",
        "contact_github": "test",
        "contact_codolio": "test",
        "report_email": "report@test.com",
        "resume_drive_link": "https://example.com/resume",
        "storage_backend": "csv",
        "dry_run": False,
        "skip_weekends": False,
        "email_interval_seconds": 0,
        "onsite_max_emails_per_day": 100,
        "remote_max_emails_per_day": 80,
        "log_level": "DEBUG",
    }
    defaults.update(overrides)
    return Settings(_env_file=None, **defaults)


def _make_dataframe(rows: int = 1) -> pd.DataFrame:
    """Create a minimal DataFrame matching scraper output."""
    data = {
        "title": [f"Engineer {i}" for i in range(rows)],
        "company": [f"Corp {i}" for i in range(rows)],
        "job_url": [f"https://example.com/job/{i}" for i in range(rows)],
        "description": [f"Build things {i}" for i in range(rows)],
        "location": [f"City {i}" for i in range(rows)],
        "is_remote": [False] * rows,
        "emails": [f"hr{i}@corp{i}.com" for i in range(rows)],
    }
    return pd.DataFrame(data)


@dataclass(frozen=True)
class FakeScrapeResult:
    jobs: pd.DataFrame
    total_raw: int = 10
    total_after_email_filter: int = 8
    total_after_title_filter: int = 6
    total_deduplicated: int = 4
    boards_queried: list = None  # type: ignore[assignment]

    def __post_init__(self) -> None:
        if self.boards_queried is None:
            object.__setattr__(self, "boards_queried", ["indeed"])


@dataclass(frozen=True)
class FakeEmailResult:
    subject: str = "Hello from Test"
    body: str = "I am interested in this role."
    mode: str = "fallback"
    word_count: int = 7


# ---------------------------------------------------------------------------
# BaseWorkflow tests
# ---------------------------------------------------------------------------


class TestBaseWorkflow:
    """Tests against OnsiteWorkflow (concrete subclass of BaseWorkflow)."""

    def _get_workflow_cls(self):
        from jobspy_v2.workflows.onsite import OnsiteWorkflow

        return OnsiteWorkflow

    @patch(f"{MODULE}.send_report")
    @patch(f"{MODULE}.create_storage_backend")
    @patch(f"{MODULE}.Deduplicator")
    @patch(f"{MODULE}.scrape_jobs")
    def test_empty_scrape_returns_zero(
        self, mock_scrape, mock_dedup_cls, mock_storage, mock_report
    ) -> None:
        mock_scrape.return_value = FakeScrapeResult(jobs=pd.DataFrame())
        settings = _make_settings()
        wf = self._get_workflow_cls()(settings)

        result = wf.run()

        assert result == 0
        mock_report.assert_called_once()
        report_kwargs = mock_report.call_args[1]
        assert report_kwargs["stats"]["emails_sent"] == 0

    @patch(f"{MODULE}.time")
    @patch(f"{MODULE}.send_report")
    @patch(f"{MODULE}.send_email", return_value=(True, None))
    @patch(f"{MODULE}.generate_email", return_value=FakeEmailResult())
    @patch(f"{MODULE}.get_valid_recipients", return_value=["hr@corp.com"])
    @patch(f"{MODULE}.create_storage_backend")
    @patch(f"{MODULE}.Deduplicator")
    @patch(f"{MODULE}.scrape_jobs")
    def test_full_pipeline_sends_email(
        self,
        mock_scrape,
        mock_dedup_cls,
        mock_storage,
        mock_recipients,
        mock_gen_email,
        mock_send_email,
        mock_report,
        mock_time,
    ) -> None:
        mock_time.monotonic.side_effect = [0.0, 10.0]
        mock_time.sleep = MagicMock()
        mock_storage.return_value.add_scraped_jobs.return_value = 2
        df = _make_dataframe(1)
        mock_scrape.return_value = FakeScrapeResult(jobs=df)
        mock_dedup = mock_dedup_cls.return_value
        mock_dedup.can_send.return_value = (True, "")

        settings = _make_settings()
        wf = self._get_workflow_cls()(settings)
        result = wf.run()

        assert result == 0
        mock_send_email.assert_called_once()
        mock_dedup.mark_sent.assert_called_once()
        mock_report.assert_called_once()
        assert mock_report.call_args[1]["stats"]["emails_sent"] == 1

    @patch(f"{MODULE}.time")
    @patch(f"{MODULE}.send_report")
    @patch(f"{MODULE}.generate_email", return_value=FakeEmailResult())
    @patch(f"{MODULE}.get_valid_recipients", return_value=["hr@corp.com"])
    @patch(f"{MODULE}.create_storage_backend")
    @patch(f"{MODULE}.Deduplicator")
    @patch(f"{MODULE}.scrape_jobs")
    def test_dry_run_skips_send(
        self,
        mock_scrape,
        mock_dedup_cls,
        mock_storage,
        mock_recipients,
        mock_gen_email,
        mock_report,
        mock_time,
    ) -> None:
        mock_time.monotonic.side_effect = [0.0, 5.0]
        mock_time.sleep = MagicMock()
        mock_storage.return_value.add_scraped_jobs.return_value = 2
        df = _make_dataframe(1)
        mock_scrape.return_value = FakeScrapeResult(jobs=df)
        mock_dedup = mock_dedup_cls.return_value
        mock_dedup.can_send.return_value = (True, "")

        settings = _make_settings(dry_run=True)
        wf = self._get_workflow_cls()(settings)
        result = wf.run()

        assert result == 0
        # send_email should NOT be called in dry-run
        # But mark_sent SHOULD be called
        mock_dedup.mark_sent.assert_called_once()
        assert mock_report.call_args[1]["stats"]["emails_sent"] == 1

    @patch(f"{MODULE}.scrape_jobs")
    @patch(f"{MODULE}.create_storage_backend")
    @patch(f"{MODULE}.Deduplicator")
    @patch(f"{MODULE}.send_report")
    @patch(f"{MODULE}.datetime")
    def test_skip_weekends(
        self, mock_dt, mock_report, mock_dedup_cls, mock_storage, mock_scrape
    ) -> None:
        # Saturday = weekday() == 5
        mock_dt.now.return_value.weekday.return_value = 5

        settings = _make_settings(skip_weekends=True)
        wf = self._get_workflow_cls()(settings)
        result = wf.run()

        assert result == 0
        mock_scrape.assert_not_called()

    @patch(f"{MODULE}.time")
    @patch(f"{MODULE}.send_report")
    @patch(f"{MODULE}.get_valid_recipients", return_value=["hr@corp.com"])
    @patch(f"{MODULE}.create_storage_backend")
    @patch(f"{MODULE}.Deduplicator")
    @patch(f"{MODULE}.scrape_jobs")
    def test_dedup_rejection_increments_skipped(
        self,
        mock_scrape,
        mock_dedup_cls,
        mock_storage,
        mock_recipients,
        mock_report,
        mock_time,
    ) -> None:
        mock_time.monotonic.side_effect = [0.0, 5.0]
        mock_storage.return_value.add_scraped_jobs.return_value = 2
        df = _make_dataframe(1)
        mock_scrape.return_value = FakeScrapeResult(jobs=df)
        mock_dedup = mock_dedup_cls.return_value
        mock_dedup.can_send.return_value = (False, "already sent")

        settings = _make_settings()
        wf = self._get_workflow_cls()(settings)
        result = wf.run()

        assert result == 0
        assert mock_report.call_args[1]["stats"]["skipped_dedup_exact"] == 1
        assert mock_report.call_args[1]["stats"]["emails_sent"] == 0

    @patch(f"{MODULE}.time")
    @patch(f"{MODULE}.send_report")
    @patch(f"{MODULE}.get_valid_recipients", return_value=[])
    @patch(f"{MODULE}.create_storage_backend")
    @patch(f"{MODULE}.Deduplicator")
    @patch(f"{MODULE}.scrape_jobs")
    def test_no_valid_emails_increments_filtered(
        self,
        mock_scrape,
        mock_dedup_cls,
        mock_storage,
        mock_recipients,
        mock_report,
        mock_time,
    ) -> None:
        mock_time.monotonic.side_effect = [0.0, 5.0]
        mock_storage.return_value.add_scraped_jobs.return_value = 2
        df = _make_dataframe(1)
        mock_scrape.return_value = FakeScrapeResult(jobs=df)

        settings = _make_settings()
        wf = self._get_workflow_cls()(settings)
        result = wf.run()

        assert result == 0
        assert mock_report.call_args[1]["stats"]["skipped_no_recipients"] == 1

    @patch(f"{MODULE}.time")
    @patch(f"{MODULE}.send_report")
    @patch(f"{MODULE}.send_email", return_value=(True, None))
    @patch(f"{MODULE}.generate_email", return_value=FakeEmailResult())
    @patch(f"{MODULE}.get_valid_recipients", return_value=["hr@corp.com"])
    @patch(f"{MODULE}.create_storage_backend")
    @patch(f"{MODULE}.Deduplicator")
    @patch(f"{MODULE}.scrape_jobs")
    def test_max_emails_cap(
        self,
        mock_scrape,
        mock_dedup_cls,
        mock_storage,
        mock_recipients,
        mock_gen_email,
        mock_send_email,
        mock_report,
        mock_time,
    ) -> None:
        mock_time.monotonic.side_effect = [0.0, 10.0]
        mock_time.sleep = MagicMock()
        mock_storage.return_value.add_scraped_jobs.return_value = 2
        # 5 jobs but max_emails = 2
        df = _make_dataframe(5)
        mock_scrape.return_value = FakeScrapeResult(jobs=df)
        mock_dedup = mock_dedup_cls.return_value
        mock_dedup.can_send.return_value = (True, "")

        settings = _make_settings(onsite_max_emails_per_day=2)
        wf = self._get_workflow_cls()(settings)
        result = wf.run()

        assert result == 0
        assert mock_send_email.call_count == 2
        assert mock_report.call_args[1]["stats"]["emails_sent"] == 2

    @patch(f"{MODULE}.time")
    @patch(f"{MODULE}.send_report")
    @patch(f"{MODULE}.send_email", return_value=(False, "SMTP error"))
    @patch(f"{MODULE}.generate_email", return_value=FakeEmailResult())
    @patch(f"{MODULE}.get_valid_recipients", return_value=["hr@corp.com"])
    @patch(f"{MODULE}.create_storage_backend")
    @patch(f"{MODULE}.Deduplicator")
    @patch(f"{MODULE}.scrape_jobs")
    def test_send_failure_increments_errors(
        self,
        mock_scrape,
        mock_dedup_cls,
        mock_storage,
        mock_recipients,
        mock_gen_email,
        mock_send_email,
        mock_report,
        mock_time,
    ) -> None:
        mock_time.monotonic.side_effect = [0.0, 5.0]
        mock_storage.return_value.add_scraped_jobs.return_value = 2
        df = _make_dataframe(1)
        mock_scrape.return_value = FakeScrapeResult(jobs=df)
        mock_dedup = mock_dedup_cls.return_value
        mock_dedup.can_send.return_value = (True, "")

        settings = _make_settings()
        wf = self._get_workflow_cls()(settings)
        result = wf.run()

        assert result == 0
        assert mock_report.call_args[1]["stats"]["emails_failed"] == 1
        assert mock_report.call_args[1]["stats"]["emails_sent"] == 0
        # mark_sent should NOT be called on failure
        mock_dedup.mark_sent.assert_not_called()

    @patch(f"{MODULE}.time")
    @patch(f"{MODULE}.send_report")
    @patch(f"{MODULE}.create_storage_backend")
    @patch(f"{MODULE}.Deduplicator")
    @patch(f"{MODULE}.scrape_jobs", side_effect=RuntimeError("API down"))
    def test_fatal_error_returns_one(
        self,
        mock_scrape,
        mock_dedup_cls,
        mock_storage,
        mock_report,
        mock_time,
    ) -> None:
        mock_time.monotonic.side_effect = [0.0, 1.0]
        settings = _make_settings()
        wf = self._get_workflow_cls()(settings)
        result = wf.run()

        assert result == 1
        assert mock_report.call_args[1]["stats"]["emails_failed"] == 1

    @patch(f"{MODULE}.time")
    @patch(f"{MODULE}.send_report")
    @patch(f"{MODULE}.send_email", return_value=(True, None))
    @patch(f"{MODULE}.generate_email", return_value=FakeEmailResult())
    @patch(f"{MODULE}.get_valid_recipients", return_value=["hr@corp.com"])
    @patch(f"{MODULE}.create_storage_backend")
    @patch(f"{MODULE}.Deduplicator")
    @patch(f"{MODULE}.scrape_jobs")
    def test_sleep_between_sends(
        self,
        mock_scrape,
        mock_dedup_cls,
        mock_storage,
        mock_recipients,
        mock_gen_email,
        mock_send_email,
        mock_report,
        mock_time,
    ) -> None:
        mock_time.monotonic.side_effect = [0.0, 10.0]
        mock_time.sleep = MagicMock()
        mock_storage.return_value.add_scraped_jobs.return_value = 2
        df = _make_dataframe(2)
        mock_scrape.return_value = FakeScrapeResult(jobs=df)
        mock_dedup = mock_dedup_cls.return_value
        mock_dedup.can_send.return_value = (True, "")

        settings = _make_settings(email_interval_seconds=30)
        wf = self._get_workflow_cls()(settings)
        wf.run()

        # Sleep called after each send where sent_count < max_emails
        assert mock_time.sleep.call_count == 2
        mock_time.sleep.assert_called_with(30)


# ---------------------------------------------------------------------------
# Onsite / Remote mode tests
# ---------------------------------------------------------------------------


class TestOnsiteWorkflow:
    def test_mode_is_onsite(self) -> None:
        from jobspy_v2.workflows.onsite import OnsiteWorkflow

        assert OnsiteWorkflow.mode == "onsite"

    @patch(f"{MODULE}.send_report")
    @patch(f"{MODULE}.create_storage_backend")
    @patch(f"{MODULE}.Deduplicator")
    @patch(f"{MODULE}.scrape_jobs")
    def test_uses_onsite_max_emails(
        self, mock_scrape, mock_dedup_cls, mock_storage, mock_report
    ) -> None:
        from jobspy_v2.workflows.onsite import OnsiteWorkflow

        mock_scrape.return_value = FakeScrapeResult(jobs=pd.DataFrame())
        settings = _make_settings(onsite_max_emails_per_day=42)
        wf = OnsiteWorkflow(settings)
        assert wf._get_max_emails() == 42


class TestRemoteWorkflow:
    def test_mode_is_remote(self) -> None:
        from jobspy_v2.workflows.remote import RemoteWorkflow

        assert RemoteWorkflow.mode == "remote"

    @patch(f"{MODULE}.send_report")
    @patch(f"{MODULE}.create_storage_backend")
    @patch(f"{MODULE}.Deduplicator")
    @patch(f"{MODULE}.scrape_jobs")
    def test_uses_remote_max_emails(
        self, mock_scrape, mock_dedup_cls, mock_storage, mock_report
    ) -> None:
        from jobspy_v2.workflows.remote import RemoteWorkflow

        mock_scrape.return_value = FakeScrapeResult(jobs=pd.DataFrame())
        settings = _make_settings(remote_max_emails_per_day=37)
        wf = RemoteWorkflow(settings)
        assert wf._get_max_emails() == 37


# ---------------------------------------------------------------------------
# CLI tests (__main__)
# ---------------------------------------------------------------------------


class TestCLI:
    @patch("jobspy_v2.__main__.get_settings")
    def test_onsite_command(self, mock_get_settings: MagicMock) -> None:
        from jobspy_v2.__main__ import main

        mock_get_settings.return_value = _make_settings()

        with patch("jobspy_v2.workflows.onsite.OnsiteWorkflow") as mock_wf:
            mock_wf.return_value.run.return_value = 0
            result = main(["onsite"])

        assert result == 0
        mock_wf.return_value.run.assert_called_once()

    @patch("jobspy_v2.__main__.get_settings")
    def test_remote_command(self, mock_get_settings: MagicMock) -> None:
        from jobspy_v2.__main__ import main

        mock_get_settings.return_value = _make_settings()

        with patch("jobspy_v2.workflows.remote.RemoteWorkflow") as mock_wf:
            mock_wf.return_value.run.return_value = 0
            result = main(["remote"])

        assert result == 0
        mock_wf.return_value.run.assert_called_once()

    @patch("jobspy_v2.__main__.get_settings")
    def test_serve_command(self, mock_get_settings: MagicMock) -> None:
        from jobspy_v2.__main__ import main

        mock_get_settings.return_value = _make_settings()

        with patch("jobspy_v2.scheduler.runner.serve") as mock_serve:
            result = main(["serve"])

        assert result == 0
        mock_serve.assert_called_once()

    @patch("jobspy_v2.__main__.get_settings")
    def test_dry_run_flag(self, mock_get_settings: MagicMock) -> None:
        from jobspy_v2.__main__ import main

        settings = _make_settings()
        mock_get_settings.return_value = settings

        with patch("jobspy_v2.workflows.onsite.OnsiteWorkflow") as mock_wf:
            mock_wf.return_value.run.return_value = 0
            main(["onsite", "--dry-run"])

        # The settings passed to OnsiteWorkflow should have dry_run=True
        passed_settings = mock_wf.call_args[0][0]
        assert passed_settings.dry_run is True

    def test_no_command_exits(self) -> None:
        from jobspy_v2.__main__ import main

        with pytest.raises(SystemExit):
            main([])
