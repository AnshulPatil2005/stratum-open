"""What the GitHub App keeps between runs, in one SQLite file.

The Action and the CLI keep nothing and get `NullStorage`. The App keeps what
DATA.md promises and no more: findings and summaries, but never code, diffs,
suggested replacements or prompts (except the opt-in prompt log); a job log
that lasts 14 days; and 👍/👎 feedback and mutes that last until a maintainer
deletes them. Every column is listed on that page, and tests/test_data_page.py
fails when one isn't.

Repos are stored as lower-case `owner/name`, logins in lower case, and times as
UTC ISO strings, which sort in time order.
"""

from __future__ import annotations

import re
import sqlite3
import threading
from contextlib import contextmanager
from datetime import datetime, timedelta, timezone
from typing import Iterator, Protocol

from stratum_open.findings import Finding

SCHEMA_VERSION = 1  # kept in `PRAGMA user_version`, so it needs no table
TABLES = ("reviews", "findings", "jobs", "votes", "mutes")
FINDING_FIELDS = ("fingerprint", "rule", "severity", "category", "path", "line_start", "line_end", "title", "detail")

# IF NOT EXISTS makes this safe to rerun after a crash, or by two processes
# opening a new file at once.
_SCHEMA = """
CREATE TABLE IF NOT EXISTS reviews (
    id INTEGER PRIMARY KEY,
    repo TEXT NOT NULL,
    pr INTEGER NOT NULL,
    head_sha TEXT NOT NULL,
    risk INTEGER NOT NULL,
    summary TEXT NOT NULL,
    created_at TEXT NOT NULL
);
CREATE INDEX IF NOT EXISTS reviews_by_pr ON reviews (repo, pr);
CREATE INDEX IF NOT EXISTS reviews_by_age ON reviews (created_at);

CREATE TABLE IF NOT EXISTS findings (
    review_id INTEGER NOT NULL REFERENCES reviews (id),
    fingerprint TEXT NOT NULL,
    rule TEXT NOT NULL,
    severity TEXT NOT NULL,
    category TEXT NOT NULL,
    path TEXT NOT NULL,
    line_start INTEGER,
    line_end INTEGER,
    title TEXT NOT NULL,
    detail TEXT NOT NULL
);
CREATE INDEX IF NOT EXISTS findings_by_review ON findings (review_id);

CREATE TABLE IF NOT EXISTS jobs (
    repo TEXT NOT NULL,
    pr INTEGER,
    event TEXT NOT NULL,
    outcome TEXT NOT NULL,
    error TEXT NOT NULL,
    prompt_log TEXT,
    created_at TEXT NOT NULL
);
CREATE INDEX IF NOT EXISTS jobs_by_age ON jobs (created_at);

CREATE TABLE IF NOT EXISTS votes (
    repo TEXT NOT NULL,
    comment_id INTEGER NOT NULL,
    voter TEXT NOT NULL,
    rule TEXT NOT NULL,
    fingerprint TEXT NOT NULL,
    vote INTEGER NOT NULL CHECK (vote IN (-1, 1)),
    voted_at TEXT NOT NULL,
    PRIMARY KEY (repo, comment_id, voter)
);

CREATE TABLE IF NOT EXISTS mutes (
    repo TEXT NOT NULL,
    rule TEXT NOT NULL,
    muted_by TEXT NOT NULL,
    muted_at TEXT NOT NULL,
    PRIMARY KEY (repo, rule)
);
"""

# Credentials that must never reach the database, even quoted inside an error
# message or a finding about a diff. Matches are replaced, not rejected, so a
# job still gets logged.
_SECRET = re.compile(
    r"""
    -----BEGIN[A-Z ]*PRIVATE\ KEY-----.*?(?:-----END[A-Z ]*PRIVATE\ KEY-----|\Z)
    | \b(?:gh[pousr]_|github_pat_)\w{20,}
    | \bsk-[\w-]{20,}
    | \beyJ[\w-]{8,}\.[\w-]{8,}\.[\w-]{8,}
    | (?i:\bbearer\s+)\S{16,}
    | (?i:\b(?:access_token|api[_-]?key|client_secret|password)=)[^\s&"']+
    """,
    re.VERBOSE | re.DOTALL,
)
REDACTED = "[redacted]"


class Storage(Protocol):
    def record_review(self, repo: str, pr: int, head_sha: str, risk: int, summary: str, findings: list[Finding]) -> None:
        """Keeps the summary and each finding's listed fields, never its
        suggestion or replacement code."""

    def record_job(self, repo: str, pr: int | None, event: str, outcome: str, error: str = "", prompt_log: str | None = None) -> None:
        """Pass `prompt_log` only when the policy sets `review.log_prompts`."""

    def record_vote(self, repo: str, rule: str, fingerprint: str, comment_id: int, voter: str, vote: int) -> None:
        """One vote per voter per comment: +1 or -1 replaces theirs, 0 removes it."""

    def rule_votes(self, repo: str) -> dict[str, tuple[int, int]]:
        """rule -> (👍, 👎)."""

    def muted_rules(self, repo: str, *, min_votes: int = 5, down_rate: float = 0.6) -> set[str]:
        """Rules muted by hand, or by at least `min_votes` votes of which at
        least `down_rate` are 👎."""

    def mute(self, repo: str, rule: str, by: str) -> None: ...

    def unmute(self, repo: str, rule: str) -> None:
        """Also clears the rule's votes; otherwise a rule muted by votes would
        stay muted."""

    def latest_findings(self, repo: str, pr: int) -> list[dict]:
        """The newest review's findings for a PR, with the FINDING_FIELDS keys."""

    def purge(self, *, findings_days: int = 180, jobs_days: int = 14, now: datetime | None = None, repo: str | None = None) -> dict[str, int]:
        """Deletes reviews (with their findings) and job-log rows past their
        windows, in one repo or all of them; never votes or mutes. Returns rows
        deleted per table."""

    def delete_pr(self, repo: str, pr: int) -> int:
        """A PR's reviews, findings and job-log rows; its feedback stays."""

    def delete_feedback(self, repo: str | None = None) -> int:
        """Votes and mutes, in every repo when `repo` is None."""

    def delete_repo(self, repo: str) -> int:
        """Every row for the repo, for an uninstall."""

    def export(self, repo: str) -> dict[str, list[dict]]:
        """Every row held for a repo, table by table."""


class NullStorage:
    """For the Action and the CLI, which keep nothing between runs."""

    def record_review(self, repo: str, pr: int, head_sha: str, risk: int, summary: str, findings: list[Finding]) -> None:
        pass

    def record_job(self, repo: str, pr: int | None, event: str, outcome: str, error: str = "", prompt_log: str | None = None) -> None:
        pass

    def record_vote(self, repo: str, rule: str, fingerprint: str, comment_id: int, voter: str, vote: int) -> None:
        pass

    def rule_votes(self, repo: str) -> dict[str, tuple[int, int]]:
        return {}

    def muted_rules(self, repo: str, *, min_votes: int = 5, down_rate: float = 0.6) -> set[str]:
        return set()

    def mute(self, repo: str, rule: str, by: str) -> None:
        pass

    def unmute(self, repo: str, rule: str) -> None:
        pass

    def latest_findings(self, repo: str, pr: int) -> list[dict]:
        return []

    def purge(self, *, findings_days: int = 180, jobs_days: int = 14, now: datetime | None = None, repo: str | None = None) -> dict[str, int]:
        return {"reviews": 0, "findings": 0, "jobs": 0}

    def delete_pr(self, repo: str, pr: int) -> int:
        return 0

    def delete_feedback(self, repo: str | None = None) -> int:
        return 0

    def delete_repo(self, repo: str) -> int:
        return 0

    def export(self, repo: str) -> dict[str, list[dict]]:
        return {table: [] for table in TABLES}


# Plain SQL, one statement per table: nothing is ever built into a query string.
_DELETE_REPO = (
    "DELETE FROM reviews WHERE repo = ?",
    "DELETE FROM jobs WHERE repo = ?",
    "DELETE FROM votes WHERE repo = ?",
    "DELETE FROM mutes WHERE repo = ?",
)
_EXPORT = {
    "reviews": "SELECT * FROM reviews WHERE repo = ? ORDER BY rowid",
    "jobs": "SELECT * FROM jobs WHERE repo = ? ORDER BY rowid",
    "votes": "SELECT * FROM votes WHERE repo = ? ORDER BY rowid",
    "mutes": "SELECT * FROM mutes WHERE repo = ? ORDER BY rowid",
}


class SQLiteStorage:
    """One connection shared by the server's threads behind a lock. WAL lets
    a second process, such as `stratum data delete`, work while it runs."""

    def __init__(self, path: str) -> None:
        self._lock = threading.Lock()
        self._db = sqlite3.connect(path, check_same_thread=False)
        self._db.row_factory = sqlite3.Row
        self._db.execute("PRAGMA journal_mode=WAL")
        self._db.execute("PRAGMA foreign_keys=ON")
        version = self._db.execute("PRAGMA user_version").fetchone()[0]
        if version > SCHEMA_VERSION:
            self._db.close()
            raise RuntimeError(f"{path} has schema version {version}; this release reads up to {SCHEMA_VERSION}.")
        if version < SCHEMA_VERSION:
            self._db.executescript(f"BEGIN;{_SCHEMA}PRAGMA user_version = {SCHEMA_VERSION};COMMIT;")

    def close(self) -> None:
        with self._lock:
            self._db.close()

    @contextmanager
    def _tx(self) -> Iterator[sqlite3.Connection]:
        """One transaction, committed on success and rolled back on error."""
        with self._lock, self._db:
            yield self._db

    def record_review(self, repo: str, pr: int, head_sha: str, risk: int, summary: str, findings: list[Finding]) -> None:
        with self._tx() as db:
            review_id = db.execute(
                "INSERT INTO reviews (repo, pr, head_sha, risk, summary, created_at) VALUES (?, ?, ?, ?, ?, ?)",
                (_repo(repo), pr, head_sha, risk, _clean(summary), _stamp()),
            ).lastrowid
            db.executemany(
                "INSERT INTO findings (review_id, fingerprint, rule, severity, category, path, line_start, line_end, title, detail)"
                " VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?)",
                [
                    (review_id, f.fingerprint, f.rule, f.severity, f.category, f.path, f.line_start, f.line_end, _clean(f.title), _clean(f.detail))
                    for f in findings
                ],
            )

    def record_job(self, repo: str, pr: int | None, event: str, outcome: str, error: str = "", prompt_log: str | None = None) -> None:
        with self._tx() as db:
            db.execute(
                "INSERT INTO jobs (repo, pr, event, outcome, error, prompt_log, created_at) VALUES (?, ?, ?, ?, ?, ?, ?)",
                (_repo(repo), pr, event, outcome, _clean(error), None if prompt_log is None else _clean(prompt_log), _stamp()),
            )

    def record_vote(self, repo: str, rule: str, fingerprint: str, comment_id: int, voter: str, vote: int) -> None:
        if vote not in (-1, 0, 1):
            raise ValueError(f"vote must be 1, -1 or 0, not {vote!r}")
        key = (_repo(repo), comment_id, voter.strip().lower())
        with self._tx() as db:
            if vote == 0:
                db.execute("DELETE FROM votes WHERE repo = ? AND comment_id = ? AND voter = ?", key)
            else:
                db.execute(
                    "INSERT OR REPLACE INTO votes (repo, comment_id, voter, rule, fingerprint, vote, voted_at) VALUES (?, ?, ?, ?, ?, ?, ?)",
                    (*key, rule, fingerprint, vote, _stamp()),
                )

    def rule_votes(self, repo: str) -> dict[str, tuple[int, int]]:
        with self._tx() as db:
            rows = db.execute("SELECT rule, SUM(vote > 0), SUM(vote < 0) FROM votes WHERE repo = ? GROUP BY rule", (_repo(repo),)).fetchall()
        return {rule: (up, down) for rule, up, down in rows}

    def muted_rules(self, repo: str, *, min_votes: int = 5, down_rate: float = 0.6) -> set[str]:
        by_votes = {rule for rule, (up, down) in self.rule_votes(repo).items() if up + down >= min_votes and down / (up + down) >= down_rate}
        with self._tx() as db:
            by_hand = {row["rule"] for row in db.execute("SELECT rule FROM mutes WHERE repo = ?", (_repo(repo),))}
        return by_hand | by_votes

    def mute(self, repo: str, rule: str, by: str) -> None:
        with self._tx() as db:
            db.execute(
                "INSERT OR REPLACE INTO mutes (repo, rule, muted_by, muted_at) VALUES (?, ?, ?, ?)",
                (_repo(repo), rule, by.strip().lower(), _stamp()),
            )

    def unmute(self, repo: str, rule: str) -> None:
        with self._tx() as db:
            db.execute("DELETE FROM mutes WHERE repo = ? AND rule = ?", (_repo(repo), rule))
            db.execute("DELETE FROM votes WHERE repo = ? AND rule = ?", (_repo(repo), rule))

    def latest_findings(self, repo: str, pr: int) -> list[dict]:
        with self._tx() as db:
            rows = db.execute(
                "SELECT fingerprint, rule, severity, category, path, line_start, line_end, title, detail FROM findings"
                " WHERE review_id = (SELECT id FROM reviews WHERE repo = ? AND pr = ? ORDER BY id DESC LIMIT 1) ORDER BY rowid",
                (_repo(repo), pr),
            ).fetchall()
        return [dict(row) for row in rows]

    def purge(self, *, findings_days: int = 180, jobs_days: int = 14, now: datetime | None = None, repo: str | None = None) -> dict[str, int]:
        now = now or datetime.now(timezone.utc)
        reviews_before = _stamp(now - timedelta(days=findings_days))
        jobs_before = _stamp(now - timedelta(days=jobs_days))
        scope = _repo(repo) if repo else None  # None: every repo
        with self._tx() as db:
            findings = db.execute(
                "DELETE FROM findings WHERE review_id IN"
                " (SELECT id FROM reviews WHERE created_at < ? AND (? IS NULL OR repo = ?))", (reviews_before, scope, scope),
            ).rowcount
            reviews = db.execute("DELETE FROM reviews WHERE created_at < ? AND (? IS NULL OR repo = ?)", (reviews_before, scope, scope)).rowcount
            jobs = db.execute("DELETE FROM jobs WHERE created_at < ? AND (? IS NULL OR repo = ?)", (jobs_before, scope, scope)).rowcount
        return {"reviews": reviews, "findings": findings, "jobs": jobs}

    def delete_pr(self, repo: str, pr: int) -> int:
        key = (_repo(repo), pr)
        with self._tx() as db:
            return (
                db.execute("DELETE FROM findings WHERE review_id IN (SELECT id FROM reviews WHERE repo = ? AND pr = ?)", key).rowcount
                + db.execute("DELETE FROM reviews WHERE repo = ? AND pr = ?", key).rowcount
                + db.execute("DELETE FROM jobs WHERE repo = ? AND pr = ?", key).rowcount
            )

    def delete_feedback(self, repo: str | None = None) -> int:
        scope = (_repo(repo) if repo else None,) * 2  # None: every repo
        with self._tx() as db:
            return (
                db.execute("DELETE FROM votes WHERE ? IS NULL OR repo = ?", scope).rowcount
                + db.execute("DELETE FROM mutes WHERE ? IS NULL OR repo = ?", scope).rowcount
            )

    def delete_repo(self, repo: str) -> int:
        key = (_repo(repo),)
        with self._tx() as db:
            deleted = db.execute("DELETE FROM findings WHERE review_id IN (SELECT id FROM reviews WHERE repo = ?)", key).rowcount
            for statement in _DELETE_REPO:
                deleted += db.execute(statement, key).rowcount
        return deleted

    def export(self, repo: str) -> dict[str, list[dict]]:
        """`SELECT *`, so a column added later is exported without a change here."""
        key = (_repo(repo),)
        with self._tx() as db:
            out = {table: [dict(row) for row in db.execute(statement, key)] for table, statement in _EXPORT.items()}
            out["findings"] = [
                dict(row) for row in db.execute(
                    "SELECT findings.* FROM findings JOIN reviews ON reviews.id = findings.review_id"
                    " WHERE reviews.repo = ? ORDER BY findings.rowid",
                    key,
                )
            ]
        return {table: out[table] for table in TABLES}


def _repo(repo: str) -> str:
    return repo.strip().lower()


def _clean(text: str) -> str:
    return _SECRET.sub(REDACTED, text)


def _stamp(moment: datetime | None = None) -> str:
    """UTC, to the second; a naive datetime is taken to be UTC already."""
    moment = moment or datetime.now(timezone.utc)
    if moment.tzinfo is None:
        moment = moment.replace(tzinfo=timezone.utc)
    return moment.astimezone(timezone.utc).strftime("%Y-%m-%dT%H:%M:%SZ")
