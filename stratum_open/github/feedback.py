"""👍/👎 on Stratum's inline comments, counted only from people with write
access, so a contributor can't vote down the findings on their own PR.

The GitHub App stores the votes on each PR it reviews (`collect_votes`). The
Action stores nothing: with `review.learn_from_history` on, it reads them at
review time from the repo's latest inline comments instead, with the
`@stratum ignore` commands maintainers gave (`learn`). Both mute a rule by
the same thresholds (`storage.muted_by_votes`).
"""

from __future__ import annotations

import re
from dataclasses import dataclass, field
from typing import Callable, Iterator

import requests

from stratum_open.findings import Finding
from stratum_open.github import publish
from stratum_open.github.api import WRITE, GitHub, GitHubError
from stratum_open.storage import Storage, muted_by_votes

VOTES = {"+1": 1, "-1": -1}
# What `learn` reads, so a review costs a few calls however busy the repo is: one call per 100 comments of each
# kind, one per comment whose reactions it reads, one per person whose access it checks.
MAX_REACTION_READS = 20
MAX_PEOPLE = 10
_NUMBER = re.compile(r"/(?:pulls|issues)/(\d+)$")


class _Access:
    """Whether a login has write access, asked once per person. At most
    `limit` people are asked; anyone past that, a bot, or someone GitHub won't
    say about counts as not having it."""

    def __init__(self, gh: GitHub, limit: int | None = None):
        self.gh, self.limit = gh, limit
        self.known: dict[str, bool] = {}

    def __call__(self, login: str) -> bool:
        key = login.lower()
        if key not in self.known:
            if key.endswith("[bot]") or (self.limit is not None and len(self.known) >= self.limit):
                return False
            try:
                self.known[key] = self.gh.permission(login) in WRITE
            except (GitHubError, requests.RequestException):
                self.known[key] = False
        return self.known[key]


def collect_votes(gh: GitHub, number: int, bot: str, storage: Storage) -> int:
    """Record every write-access 👍/👎 on this PR's Stratum comments. Returns
    how many votes were recorded."""
    maintainer = _Access(gh)
    recorded = 0
    for comment, fp, rule in _marked(gh.review_comments(number), bot):
        totals = comment.get("reactions") or {}
        if not (totals.get("+1") or totals.get("-1")):
            continue
        for voter, vote in _votes(gh, comment["id"], maintainer):
            storage.record_vote(gh.repo, rule, fp, comment["id"], voter, vote)
            recorded += 1
    return recorded


def _marked(comments: list[dict], bot: str) -> list[tuple[dict, str, str]]:
    """Stratum's own inline comments, each with its finding's (fingerprint, rule)."""
    found = []
    for comment in comments:
        match = publish._FINDING.search(comment.get("body") or "") if publish._is_bot(comment, bot) else None
        if match:
            found.append((comment, *match.groups()))
    return found


def _votes(gh: GitHub, comment_id: int, maintainer: Callable[[str], bool]) -> Iterator[tuple[str, int]]:
    """(voter, vote) for each 👍/👎 on a comment by someone with write access."""
    for reaction in gh.reactions(comment_id):
        vote = VOTES.get(reaction.get("content"))
        voter = (reaction.get("user") or {}).get("login", "")
        if vote is not None and voter and maintainer(voter):
            yield voter, vote


# ── the Action: learning from the repo's own history ─────────────────────


@dataclass
class Learned:
    """What maintainers rejected in the repo's latest comments."""
    rules: dict[str, list[int]] = field(default_factory=dict)  # a rule muted by their votes -> the PRs where it was rejected
    fingerprints: dict[str, list[int]] = field(default_factory=dict)  # a model finding rejected on its own code -> those PRs
    read: int = 0  # inline comments read
    problem: str = ""  # what couldn't be read, if anything

    def rejected_on(self, finding: Finding) -> list[int] | None:
        """The PRs where maintainers rejected findings like this one; None
        when it isn't hidden. A check's finding goes by its rule alone; one
        from the model by its rule too (its title as a slug), or by its
        fingerprint (its category, file and code) when a maintainer rejected
        that same finding before."""
        if finding.rule in self.rules:
            return self.rules[finding.rule]
        if finding.source == "model":
            return self.fingerprints.get(finding.fingerprint)
        return None


def learn(gh: GitHub, bot: str, *, lookback: int = 100) -> Learned:
    """Read the repo's latest `lookback` inline comments and `lookback`
    issue and PR comments, and work out what maintainers rejected:

    - a 👎 on one of Stratum's inline comments, or an `@stratum ignore` reply
      to it, is a vote against that finding and its rule; a 👍 is one for;
    - `@stratum ignore <rule>` on a PR is a vote against the rule there.

    Only people with write access count, as for the App's votes and for the
    commands, and each counts once per comment (or once per PR, for an ignored
    rule). A rule is muted by the App's thresholds. A model finding is hidden
    by its fingerprint once maintainers voted against it more than for it.
    GitHub refusing a read (a rate limit, a token without access) hides less,
    or nothing, and `problem` says so."""
    from stratum_open.github.commands import parse  # commands imports the runner, which imports this

    learned = Learned()
    try:
        comments = gh.recent_review_comments(lookback)
    except (GitHubError, requests.RequestException) as exc:
        learned.problem = f"Couldn't read the repo's earlier inline comments ({_why(exc)}), so none were learned from."
        return learned
    learned.read = len(comments)
    maintainer = _Access(gh, MAX_PEOPLE)
    marked = _marked(comments, bot)
    about = {comment["id"]: (fp, rule, _number(comment.get("pull_request_url"))) for comment, fp, rule in marked}

    # Reactions take a call per comment: the 👎'd ones first, then the 👍'd ones that could outvote them.
    def totals(comment: dict) -> tuple[int, int]:
        counted = comment.get("reactions") or {}
        return int(counted.get("+1") or 0), int(counted.get("-1") or 0)

    down = [m for m in marked if totals(m[0])[1]]
    contested = {m[1] for m in down} | {m[2] for m in down}
    up = [m for m in marked if not totals(m[0])[1] and totals(m[0])[0] and (m[1] in contested or m[2] in contested)]
    votes: dict[tuple[int, str], int] = {}  # (comment, voter) -> vote
    failed = 0
    for comment, _, _ in (down + up)[:MAX_REACTION_READS]:
        try:
            for voter, vote in _votes(gh, comment["id"], maintainer):
                votes[(comment["id"], voter.lower())] = vote
        except (GitHubError, requests.RequestException):
            failed += 1
    for comment in comments:  # an ignore outranks a reaction by the same person
        root, login = comment.get("in_reply_to_id"), (comment.get("user") or {}).get("login") or ""
        command = parse(comment.get("body") or "")
        if root in about and command and command[0] == "ignore" and login and maintainer(login):
            votes[(root, login.lower())] = -1

    rules, findings = _Tally(), _Tally()
    for (comment_id, _), vote in votes.items():
        fp, rule, number = about[comment_id]
        rules.add(rule, vote, number)
        if rule.startswith("model:"):  # a check's finding goes by its rule alone
            findings.add(fp, vote, number)

    try:
        commands = gh.recent_issue_comments(lookback)
    except (GitHubError, requests.RequestException) as exc:
        commands = []
        learned.problem = f"Couldn't read the repo's earlier PR comments ({_why(exc)}), so `@stratum ignore <rule>` there wasn't counted."
    ignored: set[tuple[int | None, str, str]] = set()  # (PR, person, rule)
    for comment in commands:
        login = (comment.get("user") or {}).get("login") or ""
        command = parse(comment.get("body") or "")
        if command and command[0] == "ignore" and command[1] and login and not publish._is_bot(comment, bot) and maintainer(login):
            ignored.add((_number(comment.get("issue_url")), login.lower(), command[1]))
    for number, _, rule in ignored:
        rules.add(rule, -1, number)

    learned.rules = {rule: rules.prs(rule) for rule in sorted(muted_by_votes(rules.votes))}
    learned.fingerprints = {fp: findings.prs(fp) for fp, (up, down) in findings.votes.items() if down > up}
    if failed and not learned.problem:
        learned.problem = f"Couldn't read the reactions on {failed} of Stratum's earlier comments, so their 👍/👎 weren't counted."
    return learned


class _Tally:
    """👍 and 👎 per rule or fingerprint, and the PRs where it got a 👎."""

    def __init__(self) -> None:
        self.votes: dict[str, tuple[int, int]] = {}
        self._prs: dict[str, set[int]] = {}

    def add(self, key: str, vote: int, number: int | None) -> None:
        up, down = self.votes.get(key, (0, 0))
        self.votes[key] = (up + (vote > 0), down + (vote < 0))
        if vote < 0 and number:
            self._prs.setdefault(key, set()).add(number)

    def prs(self, key: str) -> list[int]:
        return sorted(self._prs.get(key, ()))


def _number(url: str | None) -> int | None:
    match = _NUMBER.search(url or "")
    return int(match.group(1)) if match else None


def _why(exc: BaseException) -> str:
    return f"GitHub answered {exc.status}" if isinstance(exc, GitHubError) else f"couldn't reach GitHub: {type(exc).__name__}"
