"""The default branch's recent commits and the files each one changed, read
once for ownership and hotspots.

Merge commits are left out: what they change is already in the commits they
bring in. So is any commit with no parent: that's the first commit, or the
cut-off of a shallow clone, and either way it shows every file as added at
once, which says nothing about who changes what.
"""

from __future__ import annotations

from dataclasses import dataclass
from datetime import datetime, timedelta, timezone

from stratum_open.track_record import _git, author_identity, commit_sha


@dataclass(frozen=True)
class Change:
    status: str  # git's letter: A, M, D, T, R (renamed) or C (copied)
    path: str
    old_path: str | None = None  # for R and C


@dataclass(frozen=True)
class Commit:
    sha: str
    author: str  # author_identity of the .mailmap'd email
    changes: tuple[Change, ...]
    subject: str = ""


def recent_commits(repo_dir: str, ref: str = "HEAD", *, window_days: int = 180, now: datetime | None = None) -> list[Commit]:
    """Non-merge commits on `ref` in the last `window_days`, newest first.
    An empty repo or a ref that doesn't exist has none."""
    if commit_sha(repo_dir, ref) is None:
        return []
    since = (now or datetime.now(timezone.utc)) - timedelta(days=window_days)
    out = _git(
        repo_dir, "log", "--no-merges", "-M", "--name-status", "-z",
        f"--since={int(since.timestamp())}", "--format=%x1e%H%x1f%P%x1f%aE%x1f%s", ref,
    )
    commits = []
    for record in out.split("\x1e"):
        header, _, body = record.partition("\0")
        if not header:
            continue
        sha, parents, email, subject = (header.split("\x1f", 3) + [""])[:4]
        if parents.strip():
            commits.append(Commit(sha, author_identity(email), _changes(body.strip("\n").split("\0")), subject.strip()))
    return commits


def _changes(fields: list[str]) -> tuple[Change, ...]:
    """`-z --name-status` gives status, path, and a second path after R or C."""
    found, i = [], 0
    while i < len(fields):
        status = fields[i]
        if not status:
            i += 1
        elif status[0] in "RC" and i + 2 < len(fields):
            found.append(Change(status[0], fields[i + 2], fields[i + 1]))
            i += 3
        elif i + 1 < len(fields):
            found.append(Change(status[0], fields[i + 1]))
            i += 2
        else:
            break
    return tuple(found)
