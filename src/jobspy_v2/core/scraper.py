"""Job scraping with python-jobspy — smart param handling per board.

Uses ``concurrent.futures.ThreadPoolExecutor`` to scrape multiple
board×location×term combinations in parallel (I/O-bound HTTP calls).
The ``SCRAPE_MAX_WORKERS`` env var (default 5) controls concurrency.
"""

from __future__ import annotations

import logging
from concurrent.futures import ThreadPoolExecutor, as_completed
from dataclasses import dataclass, field
from typing import TYPE_CHECKING

import pandas as pd
from jobspy import scrape_jobs as jobspy_scrape

from jobspy_v2.utils.email_utils import extract_emails, get_valid_recipients

if TYPE_CHECKING:
    from jobspy_v2.config.settings import Settings

logger = logging.getLogger(__name__)


# ── Indeed API limitation ──────────────────────────────────────────────
# Indeed only supports ONE of these param groups per request:
#   1. hours_old
#   2. job_type + is_remote
#   3. easy_apply
# We prioritise job_type+is_remote over hours_old to get relevant results.
INDEED_EXCLUSIVE_PARAMS = {"hours_old", "job_type", "is_remote", "easy_apply"}


@dataclass(frozen=True)
class ScrapeResult:
    """Immutable container for scrape output."""

    jobs: pd.DataFrame
    total_raw: int = 0
    total_after_email_filter: int = 0
    total_after_title_filter: int = 0
    total_deduplicated: int = 0
    boards_queried: list[str] = field(default_factory=list)


def _get_countries_indeed(settings: Settings, mode: str) -> list[str]:
    """Get list of countries to scrape for Indeed."""
    prefix = mode.lower()
    countries = getattr(settings, f"{prefix}_countries_indeed", [])
    if not countries and prefix == "remote":
        countries = [getattr(settings, f"{prefix}_country_indeed", "USA")]
    elif not countries:
        countries = [getattr(settings, f"{prefix}_country_indeed", "")]
    return [c for c in countries if c]


def _build_base_params(settings: Settings, mode: str, country_indeed: str = "") -> dict:
    """Build base params from settings for the given mode (onsite/remote)."""
    prefix = mode.lower()
    params: dict = {
        "results_wanted": getattr(settings, f"{prefix}_results_wanted"),
        "linkedin_fetch_description": True,
        "verbose": 0,
    }

    if country_indeed:
        params["country_indeed"] = country_indeed

    hours_old = getattr(settings, f"{prefix}_hours_old", None)
    if hours_old:
        params["hours_old"] = hours_old

    job_type = getattr(settings, f"{prefix}_job_type", None)
    if job_type:
        params["job_type"] = job_type

    is_remote = getattr(settings, f"{prefix}_is_remote", False)
    if is_remote:
        params["is_remote"] = True

    if settings.proxy_list:
        params["proxies"] = settings.proxy_list

    return params


def _adapt_params_for_board(params: dict, board: str) -> dict:
    """Adjust params for board-specific limitations."""
    adapted = dict(params)

    if board == "indeed":
        # Indeed: only ONE of hours_old / (job_type+is_remote) / easy_apply
        # We keep job_type+is_remote, drop the rest
        adapted.pop("hours_old", None)
        adapted.pop("easy_apply", None)

    if board == "google":
        # Google only uses google_search_term, not search_term
        search = adapted.pop("search_term", None)
        if search:
            adapted["google_search_term"] = search

    if board == "linkedin":
        # LinkedIn: easy_apply filter no longer works
        adapted.pop("easy_apply", None)

    return adapted


def _has_valid_emails(emails_str: str | None) -> bool:
    """Check if the emails field contains at least one valid email."""
    if not emails_str or pd.isna(emails_str):
        return False
    return len(extract_emails(str(emails_str))) > 0


def _should_reject_title(title: str | None, reject_patterns: list[str]) -> bool:
    """Check if a job title matches any reject pattern (case-insensitive)."""
    if not title:
        return False
    title_lower = title.lower()
    return any(pattern.lower() in title_lower for pattern in reject_patterns)


def _scrape_single(
    params: dict, board: str, location: str, term: str
) -> pd.DataFrame | None:
    """Execute a single jobspy scrape call (designed to run in a thread)."""
    logger.debug("Scraping %s | location=%s | term='%s'", board, location, term)
    try:
        df = jobspy_scrape(**params)
        if df is not None and not df.empty:
            logger.debug("  → %d results from %s", len(df), board)
            return df
        logger.debug("  → 0 results from %s", board)
    except Exception:
        logger.warning("  → Error scraping %s for '%s' in %s", board, term, location)
    return None


def scrape_jobs(settings: Settings, mode: str) -> ScrapeResult:
    """
    Scrape jobs for the given mode (onsite/remote).

    Pipeline:
    1. Build param combos for locations × search_terms × boards
    2. Dispatch all combos to a ThreadPoolExecutor (parallel I/O)
    3. Filter: has valid email → reject titles → filter emails → dedup by job_url
    """
    prefix = mode.lower()
    search_terms: list[str] = getattr(settings, f"{prefix}_search_terms")
    boards: list[str] = getattr(settings, f"{prefix}_job_boards")

    # Locations: both onsite and remote support lists
    if prefix == "onsite":
        locations: list[str] = settings.onsite_locations
    else:
        locations: list[str] = settings.remote_locations

    # Get countries for Indeed (remote mode supports multiple)
    countries_indeed = _get_countries_indeed(settings, mode)
    if countries_indeed and "indeed" in boards:
        logger.info(
            "[%s] Indeed will scrape %d countries: %s",
            mode,
            len(countries_indeed),
            ", ".join(countries_indeed),
        )

    logger.info(
        "[%s] Starting scrape: %d terms × %d boards × %d locations",
        mode,
        len(search_terms),
        len(boards),
        len(locations),
    )
    logger.debug("Terms: %s", search_terms)
    logger.debug("Boards: %s", boards)
    logger.debug("Locations: %s", locations)

    # ── Build all param combinations ───────────────────────────────────
    tasks: list[tuple[dict, str, str, str]] = []
    for location in locations:
        for term in search_terms:
            for board in boards:
                if board in ("indeed", "glassdoor") and countries_indeed:
                    for country in countries_indeed:
                        params = _adapt_params_for_board(
                            {
                                **_build_base_params(settings, mode, country),
                                "search_term": term,
                            },
                            board,
                        )
                        params["site_name"] = [board]
                        params["location"] = location
                        location_label = f"{location} ({country})"
                        tasks.append((params, board, location_label, term))
                else:
                    base_params = _build_base_params(settings, mode)
                    params = _adapt_params_for_board(
                        {**base_params, "search_term": term}, board
                    )
                    params["site_name"] = [board]
                    params["location"] = location
                    tasks.append((params, board, location, term))

    # ── Parallel scraping ──────────────────────────────────────────────
    all_frames: list[pd.DataFrame] = []
    boards_queried: list[str] = []
    max_workers = min(settings.scrape_max_workers, len(tasks)) if tasks else 1

    logger.info(
        "Dispatching %d scrape tasks across %d workers", len(tasks), max_workers
    )

    with ThreadPoolExecutor(max_workers=max_workers) as pool:
        future_to_board = {
            pool.submit(_scrape_single, params, board, loc, term): board
            for params, board, loc, term in tasks
        }
        for future in as_completed(future_to_board):
            board = future_to_board[future]
            try:
                df = future.result()
                if df is not None:
                    all_frames.append(df)
                    if board not in boards_queried:
                        boards_queried.append(board)
            except Exception:
                logger.exception("Unexpected error collecting result for %s", board)

    if not all_frames:
        logger.warning("No jobs found across any board/location/term combo")
        return ScrapeResult(
            jobs=pd.DataFrame(),
            boards_queried=boards_queried,
        )

    combined = pd.concat(all_frames, ignore_index=True)
    total_raw = len(combined)
    logger.info("[%s] Raw scrape results: %d jobs", mode, total_raw)

    # ── Filter: must have valid email ──────────────────────────────────
    if "emails" in combined.columns:
        combined = combined[combined["emails"].apply(_has_valid_emails)]
    else:
        logger.warning("No 'emails' column in results — returning empty")
        return ScrapeResult(
            jobs=pd.DataFrame(),
            total_raw=total_raw,
            boards_queried=boards_queried,
        )
    total_after_email_filter = len(combined)
    filtered_email = total_raw - total_after_email_filter
    logger.info(
        "[%s] After email filter: %d (removed %d no-email)",
        mode,
        total_after_email_filter,
        filtered_email,
    )

    # ── Filter: reject unwanted titles ─────────────────────────────────
    reject_patterns = settings.reject_titles
    if reject_patterns:
        mask = combined["title"].apply(
            lambda t: not _should_reject_title(t, reject_patterns)
        )
        rejected_count = (~mask).sum()
        if rejected_count > 0:
            logger.info("[%s] Title filter rejected: %d", mode, rejected_count)
        combined = combined[mask]
    total_after_title_filter = len(combined)

    # ── Filter: email filter patterns ──────────────────────────────────
    email_filters = settings.email_filter_patterns
    if email_filters:

        def _filter_emails_in_row(emails_str: str) -> str:
            valid = get_valid_recipients(str(emails_str), email_filters)
            return ",".join(valid)

        combined["emails"] = combined["emails"].apply(_filter_emails_in_row)
        combined = combined[combined["emails"].str.len() > 0]

    # ── Dedup by job_url ───────────────────────────────────────────────
    if "job_url" in combined.columns:
        before_dedup = len(combined)
        combined = combined.drop_duplicates(subset=["job_url"], keep="first")
        total_deduplicated = len(combined)
        dupes = before_dedup - total_deduplicated
        if dupes > 0:
            logger.info("[%s] Dedup removed: %d duplicate URLs", mode, dupes)
    else:
        total_deduplicated = len(combined)

    combined = combined.reset_index(drop=True)
    logger.info("[%s] Final jobs to process: %d", mode, total_deduplicated)

    return ScrapeResult(
        jobs=combined,
        total_raw=total_raw,
        total_after_email_filter=total_after_email_filter,
        total_after_title_filter=total_after_title_filter,
        total_deduplicated=total_deduplicated,
        boards_queried=boards_queried,
    )
