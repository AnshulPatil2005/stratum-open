"""Who knows each module, from the default branch's recent commits.

When one person has made nearly every recent change to the code a PR
touches, they're the one who knows what it's for and what breaks it, and
nobody else does: worth asking them to review. A module is a file's first two
folders (`backend/api/x.py` is in `backend/api`). A commit counts once for
each module it touches, and a rename counts for both ends.

Authors are `author_identity` of the `.mailmap`'d email, so someone who
commits under a personal email shows as that email and can't be matched to
their GitHub login; the note then names them by email, possibly to
themselves. Bots (`…[bot]`) and `exclude`d identities aren't counted.

`bus_factor` puts a number on the same risk for any commit counts, these or
an embedder's own: the fewest authors who together made at least half of
the commits.
"""

from __future__ import annotations

from dataclasses import dataclass, field
from datetime import datetime
from typing import Iterable

from stratum_open.repo.history import recent_commits
from stratum_open.track_record import author_identity

SOLE_SHARE = 0.8  # of a module's commits, for one author to count as its only expert
MIN_COMMITS = 5  # fewer than this says too little about who knows a module
BUS_FACTOR_SHARE = 0.5  # of the commits, that the bus factor's authors made together


@dataclass
class ModuleOwners:
    module: str
    window_days: int
    authors: dict[str, int] = field(default_factory=dict)  # identity -> commits in the window

    @property
    def commits(self) -> int:
        return sum(self.authors.values())

    def top(self) -> tuple[str, int]:
        """The author with the most commits; a tie goes to the first by name."""
        return ranked(self.authors)[0]


def ranked(authors: dict[str, int]) -> list[tuple[str, int]]:
    """(author, commits), most commits first; a tie goes to the first by name."""
    return sorted(authors.items(), key=lambda item: (-item[1], item[0]))


def bus_factor(authors: dict[str, int]) -> int:
    """The fewest authors who together made at least half of the commits:
    1 when one person made half or more of them, 0 when nobody committed."""
    total, covered = sum(authors.values()), 0
    for count, (_, commits) in enumerate(ranked(authors), start=1):
        covered += commits
        if total and covered >= BUS_FACTOR_SHARE * total:
            return count
    return 0


def module_of(path: str) -> str:
    folders = path.strip("/").split("/")[:-1]
    return "/".join(folders[:2]) or "."


def owners(
    repo_dir: str,
    ref: str = "HEAD",
    *,
    window_days: int = 180,
    now: datetime | None = None,
    exclude: tuple[str, ...] | list[str] = (),
) -> dict[str, ModuleOwners]:
    """Commits per author for every module changed on `ref` in the last
    `window_days`, keyed by module."""
    skip = {author_identity(item) for item in exclude}
    found: dict[str, ModuleOwners] = {}
    for commit in recent_commits(repo_dir, ref, window_days=window_days, now=now):
        if commit.author in skip or commit.author.endswith("[bot]"):
            continue
        touched = {module_of(path) for change in commit.changes for path in (change.path, change.old_path) if path}
        for module in touched:
            entry = found.setdefault(module, ModuleOwners(module, window_days))
            entry.authors[commit.author] = entry.authors.get(commit.author, 0) + 1
    return found


def bus_factor_notes(owners: dict[str, ModuleOwners], changed_paths: Iterable[str], pr_author: str | Iterable[str]) -> list[str]:
    """A line for each module the PR touches that one other person has
    written almost alone lately. Nothing when that person wrote the PR.

    `pr_author` is the author's login, or the login and the emails GitHub
    links to it: history knows people by email, so the login alone misses
    authors who don't commit with GitHub's noreply address."""
    names = [pr_author] if isinstance(pr_author, str) else list(pr_author)
    authors = {author_identity(name) for name in names if name}
    notes = []
    for module in dict.fromkeys(module_of(path) for path in changed_paths):
        entry = owners.get(module)
        if entry is None or entry.commits < MIN_COMMITS:
            continue
        owner, count = entry.top()
        if owner in authors or count / entry.commits < SOLE_SHARE:
            continue
        notes.append(
            f"Only {_name(owner)} has changed {_where(module)} in the last {entry.window_days} days "
            f"({count} of {entry.commits} commits); a maintainer may want them to review this."
        )
    return notes


def _name(identity: str) -> str:
    """A login in code style, which doesn't notify them on every PR; never an
    email, which this note would otherwise publish in a PR comment."""
    return "one person" if "@" in identity else f"`{identity}`"


def _where(module: str) -> str:
    return "the top-level files" if module == "." else f"`{module}`"
