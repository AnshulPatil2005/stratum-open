"""Small helpers with no state of their own, shared by the GitHub client, the
model client and the storage layer, so that none of them has to import the
others just to redact a string or read a `Retry-After` header."""

from __future__ import annotations

import re
from datetime import datetime, timezone
from email.utils import parsedate_to_datetime

# Credentials that must never be written down, even quoted inside an error
# message, a log line or a finding about a diff. Matches are replaced, not
# rejected, so a job still gets logged.
_SECRET = re.compile(
    r"""
    -----BEGIN[A-Z ]*PRIVATE\ KEY-----.*?(?:-----END[A-Z ]*PRIVATE\ KEY-----|\Z)
    | \b(?:gh[pousr]_|github_pat_)\w{20,}
    | \bsk-[\w-]{20,}
    | \beyJ[\w-]{8,}\.[\w-]{8,}\.[\w-]{8,}
    | (?i:\bbearer\s+)\S{16,}
    | (?i:\bbasic\s+)[A-Za-z0-9+/=]{16,}
    | (?i:\b(?:access_token|api[_-]?key|client_secret|password)=)[^\s&"']+
    """,
    re.VERBOSE | re.DOTALL,
)
REDACTED = "[redacted]"


def redact(text: str) -> str:
    """`text` with anything shaped like a credential replaced, for anything
    that's written down: the database, a log line, an error message."""
    return _SECRET.sub(REDACTED, text or "")


def retry_after(header: str | None, default: float) -> float:
    """Seconds to wait from a `Retry-After` header, which is either a number
    of seconds or an HTTP date. Anything unreadable means `default`."""
    if not header:
        return default
    header = header.strip()
    try:
        return max(0.0, float(header))
    except ValueError:
        pass
    try:
        when = parsedate_to_datetime(header)
    except (TypeError, ValueError, IndexError):
        return default
    if when.tzinfo is None:
        when = when.replace(tzinfo=timezone.utc)
    return max(0.0, (when - datetime.now(timezone.utc)).total_seconds())
