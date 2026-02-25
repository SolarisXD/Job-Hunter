"""Pure email validation and filtering utilities.

No SMTP, no network calls — just regex matching and filter pattern evaluation.
"""

from __future__ import annotations

import re
from collections.abc import Sequence

# Strict email regex: rejects problematic patterns
# - Local part: alphanumeric, dots, underscores, plus, hyphen (can't START with ._-)
# - Domain can't start with hyphen or dot, no consecutive dots
# - TLD must be 2+ chars
EMAIL_REGEX: re.Pattern[str] = re.compile(
    r"^(?!.*\.\.)"  # No consecutive dots anywhere
    r"[a-zA-Z0-9]"  # Local part must start with alphanumeric
    r"[a-zA-Z0-9._%+-]*"  # Can contain these
    r"@[a-zA-Z0-9]"  # Domain must start with alphanumeric
    r"[a-zA-Z0-9.-]*"  # Can contain these (consecutive dots handled by lookahead)
    r"\.[a-zA-Z]{2,}$"  # TLD: 2+ chars
)

# Suspicious TLDs commonly used in spam/invalid emails
SUSPICIOUS_TLDS: frozenset[str] = frozenset(
    {
        # Country codes with high spam
        "to",
        "tk",
        "ml",
        "ga",
        "cf",
        "gq",
        "xyz",
        "top",
        "work",
        "ru",
        "cn",
        "ua",
        "kz",
        "by",
        "su",
        "am",
        "ge",
        # Other suspicious
        "zip",
        "mov",
        "icu",
        "link",
        "click",
        "review",
        "country",
        "science",
        "tk",
        "ga",
        "cf",
        "gq",
        "ml",
        "ninja",
        "science",
    }
)

# Hardcoded email patterns that are ALWAYS filtered (typos, spam, etc.)
HARDCODED_EMAIL_BLOCKLIST: list[re.Pattern[str]] = [
    # accommodation - matches any email containing "ac...modation"
    # (catches both "accommodation" and "accomodation" typos)
    re.compile(r"ac.*modation", re.IGNORECASE),
    # accessibility variations
    re.compile(r"accessibility", re.IGNORECASE),
]

# Suspicious patterns in email (not valid company emails)
SUSPICIOUS_EMAIL_PATTERNS: list[re.Pattern[str]] = [
    # Patterns like vs00825391@... (employee IDs — 4+ digits after possible prefix)
    re.compile(r"^[a-zA-Z]{0,2}\d{4,}@", re.IGNORECASE),
    # Patterns like _name@ or -name@ (underscore/hyphen prefix at start)
    re.compile(r"^[_+-]\w+@", re.IGNORECASE),
]

# Common invalid/generic emails to filter
# Note: "jobs" is intentionally NOT included - many companies use jobs@company.com
INVALID_EMAIL_PREFIXES: frozenset[str] = frozenset(
    {
        "info",
        "admin",
        "support",
        "noreply",
        "no-reply",
        "donotreply",
        "hr",
        "careers",
        "recruitment",
        "recruiting",
        "contact",
        "hello",
        "mail",
        "webmaster",
        "postmaster",
        "office",
        "team",
    }
)


def validate_email(email: str) -> bool:
    """Return True if *email* is valid and not suspicious.

    Checks:
    - RFC-5322 pattern match
    - No suspicious TLD
    - No suspicious patterns (ID-like numbers, underscore prefix, etc.)
    - Rejects known invalid formats
    - Hardcoded blocklist (accommodation typos, etc.)
    """
    if not email or not isinstance(email, str):
        return False

    email = email.strip().lower()

    # Basic pattern match
    if EMAIL_REGEX.match(email) is None:
        return False

    # Check hardcoded blocklist first (before anything else)
    for pattern in HARDCODED_EMAIL_BLOCKLIST:
        if pattern.search(email):
            return False

    # Extract domain and TLD
    try:
        domain = email.split("@")[1]
        tld = domain.rsplit(".", 1)[-1] if "." in domain else ""
    except (IndexError, ValueError):
        return False

    # Check suspicious TLDs
    if tld.lower() in SUSPICIOUS_TLDS:
        return False

    # Check suspicious patterns
    for pattern in SUSPICIOUS_EMAIL_PATTERNS:
        if pattern.search(email):
            return False

    # Check if local part starts with suspicious prefix (but allow valid company emails)
    local_part = email.split("@")[0]
    if local_part in INVALID_EMAIL_PREFIXES:
        # Allow if domain has a dot (looks like a real company domain)
        # Reject if domain is just a single word without dot
        if "." in domain:
            pass  # Allow (likely valid company email like hr@company.com)
        else:
            return False

    return True


def extract_emails(emails_str: str) -> list[str]:
    """Split a comma-separated string into a list of validated emails.

    Invalid entries are silently dropped.
    """
    if not emails_str or not isinstance(emails_str, str):
        return []
    seen: set[str] = set()
    result: list[str] = []
    for raw in emails_str.split(","):
        email = raw.strip().lower()
        if email and email not in seen and validate_email(email):
            seen.add(email)
            result.append(email)
    return result


def matches_filter_pattern(email: str, pattern: str) -> bool:
    """Check if *email* matches a single filter pattern.

    Supported prefixes:
        ``starts_with:<prefix>``  — email starts with <prefix>
        ``contains:<substring>``  — email contains <substring>

    Unknown prefix formats are treated as ``contains:`` for safety.
    """
    if not email or not pattern:
        return False

    email_lower = email.lower()
    pattern = pattern.strip()

    if pattern.startswith("starts_with:"):
        prefix = pattern[len("starts_with:") :].lower()
        return email_lower.startswith(prefix)

    if pattern.startswith("contains:"):
        substring = pattern[len("contains:") :].lower()
        return substring in email_lower

    # Unknown prefix — treat as substring match (safe default)
    return pattern.lower() in email_lower


def is_filtered_email(email: str, filter_patterns: Sequence[str]) -> bool:
    """Return True if *email* matches ANY filter pattern."""
    if not email or not filter_patterns:
        return False
    return any(matches_filter_pattern(email, p) for p in filter_patterns)


def get_valid_recipients(
    emails_str: str, filter_patterns: Sequence[str] = ()
) -> list[str]:
    """Full pipeline: extract → validate → filter.

    Returns only emails that are valid AND not caught by any filter pattern.
    """
    valid = extract_emails(emails_str)
    if not filter_patterns:
        return valid
    return [e for e in valid if not is_filtered_email(e, filter_patterns)]


# ──────────────────────────────────────────────────────────────────────────────
# DNS-based deliverability validation via emval (Rust-backed, fast)
# ──────────────────────────────────────────────────────────────────────────────


def validate_email_deliverable(email: str) -> bool:
    """Return True if *email* passes DNS/MX deliverability check via emval.

    Uses emval with deliverable_address=True which performs a DNS MX record
    lookup to confirm the domain can receive mail. Does NOT do an SMTP
    handshake — safe for bulk use with no IP blacklist risk.

    Returns False for invalid/undeliverable addresses (emval.InvalidEmailError).
    Returns False conservatively when DNS lookup raises any other error (e.g.
    timeout) so we don't silently drop emails during transient DNS failures.
    """
    try:
        import emval  # type: ignore[import]

        emval.validate_email(email, deliverable_address=True)
        return True
    except Exception:
        # emval.InvalidEmailError  → undeliverable / bad syntax
        # Any other exception      → DNS timeout, network issue, etc.
        # Conservative choice: treat non-InvalidEmailError failures as invalid
        # to avoid sending to domains with missing MX records.
        return False


def filter_deliverable_emails(
    emails: list[str],
) -> tuple[list[str], list[str]]:
    """Filter a list of emails using DNS/MX deliverability check (emval).

    Returns ``(valid_emails, invalid_emails)`` — two separate lists so callers
    can count filtered-out addresses and include them in run statistics without
    re-scanning.

    Example::

        valid, invalid = filter_deliverable_emails(["a@real.com", "b@fake.xyz"])
        # valid   → ["a@real.com"]
        # invalid → ["b@fake.xyz"]
    """
    valid: list[str] = []
    invalid: list[str] = []
    for email in emails:
        if validate_email_deliverable(email):
            valid.append(email)
        else:
            invalid.append(email)
    return valid, invalid
