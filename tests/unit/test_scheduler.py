"""Tests for scheduler package — cron_scheduler, health_check, runner."""

from __future__ import annotations

import urllib.request
from unittest.mock import MagicMock, patch

import pytest

from jobspy_v2.scheduler.cron_scheduler import CronScheduler, parse_cron
from jobspy_v2.scheduler.health_check import start_health_check

# ── parse_cron ──────────────────────────────────────────────────────────


class TestParseCron:
    def test_valid_expression(self) -> None:
        result = parse_cron("30 2 * * *")
        assert result == {
            "minute": "30",
            "hour": "2",
            "day": "*",
            "month": "*",
            "day_of_week": "*",
        }

    def test_all_wildcards(self) -> None:
        result = parse_cron("* * * * *")
        assert all(v == "*" for v in result.values())
        assert len(result) == 5

    def test_complex_expression(self) -> None:
        result = parse_cron("0 13 1-15 */2 mon-fri")
        assert result["minute"] == "0"
        assert result["hour"] == "13"
        assert result["day"] == "1-15"
        assert result["month"] == "*/2"
        assert result["day_of_week"] == "mon-fri"

    def test_too_few_fields_raises(self) -> None:
        with pytest.raises(ValueError, match="5 fields"):
            parse_cron("30 2 *")

    def test_too_many_fields_raises(self) -> None:
        with pytest.raises(ValueError, match="5 fields"):
            parse_cron("30 2 * * * *")

    def test_empty_string_raises(self) -> None:
        with pytest.raises(ValueError, match="5 fields"):
            parse_cron("")

    def test_strips_whitespace(self) -> None:
        result = parse_cron("  30   2   *   *   *  ")
        assert result["minute"] == "30"
        assert result["hour"] == "2"


# ── CronScheduler ──────────────────────────────────────────────────────


class TestCronScheduler:
    @patch("jobspy_v2.scheduler.cron_scheduler.BackgroundScheduler")
    def test_init_creates_background_scheduler(self, mock_bg: MagicMock) -> None:
        CronScheduler()
        mock_bg.assert_called_once_with(timezone="UTC")

    @patch("jobspy_v2.scheduler.cron_scheduler.BackgroundScheduler")
    def test_add_job_delegates(self, mock_bg_cls: MagicMock) -> None:
        mock_sched = mock_bg_cls.return_value
        cs = CronScheduler()
        func = MagicMock()

        cs.add_job(func, "30 2 * * *", "test-job", extra="arg")

        mock_sched.add_job.assert_called_once()
        call_kwargs = mock_sched.add_job.call_args
        assert call_kwargs[1]["id"] == "test-job"
        assert call_kwargs[1]["kwargs"] == {"extra": "arg"}

    @patch("jobspy_v2.scheduler.cron_scheduler.BackgroundScheduler")
    def test_start_delegates(self, mock_bg_cls: MagicMock) -> None:
        cs = CronScheduler()
        cs.start()
        mock_bg_cls.return_value.start.assert_called_once()

    @patch("jobspy_v2.scheduler.cron_scheduler.BackgroundScheduler")
    def test_shutdown_delegates(self, mock_bg_cls: MagicMock) -> None:
        cs = CronScheduler()
        cs.shutdown(wait=False)
        mock_bg_cls.return_value.shutdown.assert_called_once_with(wait=False)

    @patch("jobspy_v2.scheduler.cron_scheduler.BackgroundScheduler")
    def test_running_property(self, mock_bg_cls: MagicMock) -> None:
        mock_bg_cls.return_value.running = True
        cs = CronScheduler()
        assert cs.running is True


# ── Health Check ────────────────────────────────────────────────────────


class TestHealthCheck:
    def test_responds_200_on_health_path(self) -> None:
        server, thread = start_health_check(port=0, path="/health")
        try:
            port = server.server_address[1]
            resp = urllib.request.urlopen(f"http://127.0.0.1:{port}/health")
            assert resp.status == 200
            assert resp.read() == b"ok"
        finally:
            server.shutdown()

    def test_responds_404_on_unknown_path(self) -> None:
        server, thread = start_health_check(port=0, path="/health")
        try:
            port = server.server_address[1]
            with pytest.raises(urllib.error.HTTPError) as exc_info:
                urllib.request.urlopen(f"http://127.0.0.1:{port}/unknown")
            assert exc_info.value.code == 404
        finally:
            server.shutdown()

    def test_custom_health_path(self) -> None:
        server, thread = start_health_check(port=0, path="/ping")
        try:
            port = server.server_address[1]
            resp = urllib.request.urlopen(f"http://127.0.0.1:{port}/ping")
            assert resp.status == 200
        finally:
            server.shutdown()


# ── Runner ──────────────────────────────────────────────────────────────


class TestRunner:
    @patch("jobspy_v2.scheduler.runner.logger")
    def test_run_workflow_success(self, mock_logger: MagicMock) -> None:
        from jobspy_v2.scheduler.runner import _run_workflow

        mock_cls = MagicMock()
        mock_cls.__name__ = "TestWorkflow"
        mock_instance = mock_cls.return_value
        mock_instance.run.return_value = 0
        mock_settings = MagicMock()

        _run_workflow(mock_cls, mock_settings)

        mock_cls.assert_called_once_with(mock_settings)
        mock_instance.run.assert_called_once()

    @patch("jobspy_v2.scheduler.runner.logger")
    def test_run_workflow_handles_exception(self, mock_logger: MagicMock) -> None:
        from jobspy_v2.scheduler.runner import _run_workflow

        mock_cls = MagicMock()
        mock_cls.__name__ = "CrashWorkflow"
        mock_cls.return_value.run.side_effect = RuntimeError("boom")
        mock_settings = MagicMock()

        # Should not raise
        _run_workflow(mock_cls, mock_settings)
        mock_logger.exception.assert_called_once()

    @patch("jobspy_v2.scheduler.runner.start_health_check")
    @patch("jobspy_v2.scheduler.runner.CronScheduler")
    def test_serve_sets_up_scheduler_and_health_check(
        self,
        mock_sched_cls: MagicMock,
        mock_hc: MagicMock,
    ) -> None:
        from jobspy_v2.scheduler.runner import serve

        mock_hc.return_value = (MagicMock(), MagicMock())
        mock_sched = mock_sched_cls.return_value

        # Make serve return quickly by setting stop_event immediately
        def instant_set(self_event, *args, **kwargs):
            self_event.set()
            return True

        settings = MagicMock()
        settings.scheduler_onsite_cron = "30 2 * * *"
        settings.scheduler_remote_cron = "0 13 * * *"
        settings.health_check_enabled = True
        settings.health_check_port = 10000
        settings.health_check_path = "/health"

        # Patch time.sleep to avoid blocking, set stop_event on first sleep
        with patch(
            "jobspy_v2.scheduler.runner.time.sleep", side_effect=KeyboardInterrupt
        ):
            with patch("jobspy_v2.scheduler.runner.signal.signal"):
                try:
                    serve(settings)
                except KeyboardInterrupt:
                    pass

        # Scheduler should have 2 jobs added
        assert mock_sched.add_job.call_count == 2
        mock_sched.start.assert_called_once()
        mock_hc.assert_called_once_with(port=10000, path="/health")
