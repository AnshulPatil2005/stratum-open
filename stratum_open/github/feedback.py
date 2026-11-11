"""👍/👎 on Stratum's inline comments, counted only from people with write
access, so a contributor can't vote down the findings on their own PR.

Only the GitHub App counts them, because it can store votes across PRs.
"""

from __future__ import annotations

from stratum_open.github import publish
from stratum_open.github.api import GitHub, GitHubError
from stratum_open.github.commands import WRITE
from stratum_open.storage import Storage

VOTES = {"+1": 1, "-1": -1}


def collect_votes(gh: GitHub, number: int, bot: str, storage: Storage) -> int:
    """Record every write-access 👍/👎 on this PR's Stratum comments. Returns
    how many votes were recorded."""
    permissions: dict[str, bool] = {}
    recorded = 0
    for comment in gh.review_comments(number):
        if not publish._is_bot(comment, bot):
            continue
        match = publish._FINDING.search(comment.get("body") or "")
        totals = comment.get("reactions") or {}
        if not match or not (totals.get("+1") or totals.get("-1")):
            continue
        fp, rule = match.groups()
        for reaction in gh.reactions(comment["id"]):
            vote = VOTES.get(reaction.get("content"))
            voter = (reaction.get("user") or {}).get("login", "")
            if vote is None or not voter:
                continue
            if voter not in permissions:
                try:
                    permissions[voter] = gh.permission(voter) in WRITE
                except GitHubError:
                    permissions[voter] = False
            if permissions[voter]:
                storage.record_vote(gh.repo, rule, fp, comment["id"], voter, vote)
                recorded += 1
    return recorded
