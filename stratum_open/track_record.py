"""Each author's track record, built from the default branch's git history alone.

A merged PR counts against its author when, later on the default branch:
- its merge or squash commit is reverted: "This reverts commit <sha>", a
  "Reverts owner/repo#<n>" line, or a GitHub revert PR (branch `revert-<n>-…`), or
- a commit landing within `followup_days` references it (`#<n>`) with "fix",
  "fixes" or "follow-up".

A commit with a `Stratum-Record: ignore` trailer counts against no one; on a
merge or squash commit it covers every commit that PR brought in. Records are
rebuilt from git whenever they're needed, and nothing here is stored.

An author is a GitHub login when the commit email shows one (GitHub's noreply
addresses), otherwise the lower-cased email; `.mailmap` is honoured. A merge
commit's PR author is whoever wrote most of the PR's commits, not the person
who merged it. PRs landed by rebase carry no PR number in git and aren't counted.
"""

from __future__ import annotations

import re
import subprocess
from collections import Counter
from dataclasses import dataclass, field
from datetime import datetime, timedelta, timezone

IGNORE_TRAILER = re.compile(r"^Stratum-Record:\s*ignore\s*$", re.IGNORECASE | re.MULTILINE)
_MERGE_SUBJECT = re.compile(r"^Merge pull request #(\d+)\b")
_SQUASH_SUBJECT = re.compile(r"\(#(\d+)\)\s*$")
_REVERTS_SHA = re.compile(r"\bThis reverts commit ([0-9a-f]{7,40})\b")
_REVERTS_PR = re.compile(r"\bReverts [\w.-]+/[\w.-]+#(\d+)\b")
_REVERT_BRANCH = re.compile(r"^Merge pull request #\d+ from [\w.-]+/revert-(\d+)-")
_REFERENCE = re.compile(r"(?<![\w/&])#(\d+)\b")
_FIX_WORD = re.compile(r"\b(?:fix|fixes|follow[- ]?up)\b", re.IGNORECASE)
_NOREPLY = re.compile(r"^(?:\d+\+)?([\w-]+(?:\[bot\])?)@users\.noreply\.github\.com$", re.IGNORECASE)


@dataclass(frozen=True)
class MergedPR:
    number: int
    sha: str  # the commit that landed it on the default branch
    author: str
    merged_at: datetime
    title: str


@dataclass(frozen=True)
class Strike:
    pr: MergedPR
    kind: str  # "revert" | "follow-up"
    commit: str  # the commit that reverted or fixed it
    subject: str


@dataclass
class AuthorRecord:
    author: str
    merged: list[MergedPR] = field(default_factory=list)
    strikes: list[Strike] = field(default_factory=list)

    def summary(self) -> str:
        struck = len({s.pr.number for s in self.strikes})
        return f"{len(self.merged)} merged PR(s), {struck} later reverted or fixed"


@dataclass(frozen=True)
class _Commit:
    sha: str
    parents: tuple[str, ...]
    email: str
    time: datetime
    message: str

    @property
    def subject(self) -> str:
        return self.message.split("\n", 1)[0]


def author_identity(email_or_login: str) -> str:
    value = email_or_login.strip()
    match = _NOREPLY.match(value)
    return (match.group(1) if match else value).lower()


def build_records(
    repo_dir: str,
    ref: str = "HEAD",
    *,
    window_days: int = 180,
    followup_days: int = 14,
    exclude: tuple[str, ...] | list[str] = (),
    now: datetime | None = None,
) -> dict[str, AuthorRecord]:
    """A record for every author with a PR landed on `ref` in the last
    `window_days`, keyed by `author_identity`."""
    since = (now or datetime.now(timezone.utc)) - timedelta(days=window_days)
    tip = commit_sha(repo_dir, ref)
    if tip is None:
        return {}  # an empty repo, or a ref that isn't there: nobody has a record yet
    by_sha = _history(repo_dir, ref, since)
    if tip not in by_sha:
        return {}

    # Walk the default branch oldest first; each landing brings in the PR
    # commits that weren't already on the branch (merge^1..merge^2).
    mainline = []
    sha = tip
    while sha in by_sha:
        mainline.append(by_sha[sha])
        parents = by_sha[sha].parents
        sha = parents[0] if parents else ""
    mainline.reverse()

    seen: set[str] = {c.sha for c in mainline}
    landings: list[tuple[_Commit, list[_Commit]]] = []
    landed: dict[int, MergedPR] = {}
    for commit in mainline:
        brought = _brought_in(commit, by_sha, seen)
        landings.append((commit, brought))
        number, author = _landed_pr(commit, brought)
        if number is not None and number not in landed:
            landed[number] = MergedPR(number, commit.sha, author, commit.time, _title(commit))

    skip = {author_identity(item) for item in exclude}
    records: dict[str, AuthorRecord] = {}
    for pr in landed.values():
        if pr.author not in skip:
            records.setdefault(pr.author, AuthorRecord(pr.author)).merged.append(pr)

    landed_by_sha = {pr.sha: pr for pr in landed.values()}
    counted: set[tuple[int, str]] = set()
    for landing, brought in landings:
        if IGNORE_TRAILER.search(landing.message):
            continue
        own = _landed_pr(landing, brought)[0]
        for commit in [*brought, landing]:
            if IGNORE_TRAILER.search(commit.message):
                continue
            for pr, kind in _strikes(commit, landing, own, landed_by_sha, landed, followup_days):
                if pr.author in skip or (pr.number, kind) in counted:
                    continue
                counted.add((pr.number, kind))
                records[pr.author].strikes.append(Strike(pr, kind, commit.sha, commit.subject))
    return records


def _strikes(commit, landing, own, landed_by_sha, landed, followup_days):
    """(PR, kind) pairs this commit counts against. A PR is only struck by
    something that landed after it, and never by its own commits."""
    def earlier(pr: MergedPR | None) -> bool:
        return pr is not None and pr.number != own and pr.merged_at <= landing.time and pr.sha != landing.sha

    reverted = [landed_by_sha[sha] for prefix in _REVERTS_SHA.findall(commit.message)
                for sha in landed_by_sha if sha.startswith(prefix)]
    reverted += [landed.get(int(n)) for n in _REVERTS_PR.findall(commit.message)]
    if commit is landing:
        reverted += [landed.get(int(n)) for n in _REVERT_BRANCH.findall(commit.subject)]
    reverted = [pr for pr in reverted if earlier(pr)]
    if reverted:
        yield from ((pr, "revert") for pr in reverted)
        return
    if not _FIX_WORD.search(commit.message):
        return
    for number in sorted({int(n) for n in _REFERENCE.findall(commit.message)}):
        pr = landed.get(number)
        if earlier(pr) and landing.time - pr.merged_at <= timedelta(days=followup_days):
            yield pr, "follow-up"


def _brought_in(landing: _Commit, by_sha: dict[str, _Commit], seen: set[str]) -> list[_Commit]:
    """Commits a merge adds to the branch, oldest first; marks them seen."""
    found, stack = [], list(landing.parents[1:])
    while stack:
        sha = stack.pop()
        if sha in seen or sha not in by_sha:
            continue
        seen.add(sha)
        found.append(by_sha[sha])
        stack.extend(by_sha[sha].parents)
    return sorted(found, key=lambda c: c.time)


def _landed_pr(commit: _Commit, brought: list[_Commit]) -> tuple[int | None, str]:
    merge = _MERGE_SUBJECT.match(commit.subject)
    if merge and len(commit.parents) > 1:
        authors = Counter(author_identity(c.email) for c in brought if len(c.parents) < 2)
        author = authors.most_common(1)[0][0] if authors else author_identity(commit.email)
        return int(merge.group(1)), author
    squash = _SQUASH_SUBJECT.search(commit.subject)
    if squash and len(commit.parents) == 1:
        return int(squash.group(1)), author_identity(commit.email)
    return None, ""


def _title(commit: _Commit) -> str:
    if _MERGE_SUBJECT.match(commit.subject):
        body = commit.message.partition("\n")[2].strip()
        return body.partition("\n")[0] or commit.subject
    return _SQUASH_SUBJECT.sub("", commit.subject).strip()


def _git(repo_dir: str, *args: str) -> str:
    return subprocess.run(
        ["git", "-C", repo_dir, *args],
        capture_output=True, text=True, encoding="utf-8", errors="replace", timeout=120, check=True,
    ).stdout


def commit_sha(repo_dir: str, ref: str) -> str | None:
    """The commit `ref` points at, or None in an empty repo or for a ref that
    isn't there."""
    try:
        return _git(repo_dir, "rev-parse", "--verify", "--quiet", f"{ref}^{{commit}}").strip()
    except subprocess.CalledProcessError:
        return None


def describe_subprocess_error(exc: BaseException, limit: int = 200) -> str:
    """One line for a git command that failed: what it wrote to stderr when
    it ran, else its exit status or timeout; for an `OSError` (git missing, a
    path that isn't there) the error itself. Never the command: the App runs
    git with an authorization header in its arguments, and `str(exc)` of a
    `CalledProcessError` would print it."""
    if isinstance(exc, OSError):
        return f"{type(exc).__name__}: {exc}"[:limit]
    stderr = getattr(exc, "stderr", None)
    if isinstance(stderr, bytes):
        stderr = stderr.decode("utf-8", errors="replace")
    detail = (stderr or "").strip()
    if not detail:
        if isinstance(exc, subprocess.TimeoutExpired):
            detail = f"timed out after {exc.timeout:g} s"
        elif isinstance(exc, subprocess.CalledProcessError):
            detail = f"exit status {exc.returncode}"
        else:
            detail = type(exc).__name__
    return detail[:limit]


def _history(repo_dir: str, ref: str, since: datetime) -> dict[str, _Commit]:
    out = _git(repo_dir, "log", f"--since={int(since.timestamp())}", "--format=%H%x1f%P%x1f%aE%x1f%ct%x1f%B%x1e", ref)
    commits = {}
    for record in out.split("\x1e"):
        record = record.strip("\n")
        if record:
            sha, parents, email, stamp, message = record.split("\x1f", 4)
            commits[sha] = _Commit(sha, tuple(parents.split()), email, datetime.fromtimestamp(int(stamp), timezone.utc), message.strip())
    return commits
