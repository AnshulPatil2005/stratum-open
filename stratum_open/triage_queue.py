"""The triage queue: one issue listing every open PR, sorted.

It's rebuilt from each PR's last Stratum summary, so it costs no model calls
and needs no database.
"""

from __future__ import annotations

from stratum_open.github import publish
from stratum_open.runner import Context
from stratum_open.triage import PRFacts, Triage, render_queue

TITLE = "Stratum triage queue"
MAX_PULLS = 100


def update_queue(ctx: Context, *, post: bool = True) -> str:
    gh = ctx.gh
    rows: list[tuple[PRFacts, Triage | None]] = []
    for pull in gh.open_pulls()[:MAX_PULLS]:
        if pull.get("draft"):
            continue
        summary = publish.find_summary(gh.issue_comments(pull["number"]), ctx.bot)
        found = publish.State.decode(summary["body"]).triage if summary else {}
        facts = PRFacts.from_pull(pull, [])
        facts.additions, facts.deletions = found.get("additions", 0), found.get("deletions", 0)
        result = None
        if found.get("key"):
            result = Triage(str(found["key"]), _strings(found.get("reasons")), _strings(found.get("evidence")), _number(found.get("score")))
        rows.append((facts, result))
    body = render_queue(rows, ctx.policy)
    return _post(ctx, body) if post else body


def _strings(value) -> list[str]:
    return [str(v) for v in value] if isinstance(value, list) else []


def _number(value) -> float:
    try:
        return float(value)
    except (TypeError, ValueError):
        return 0.0


def _post(ctx: Context, body: str) -> str:
    """Edit the queue issue in place when there is one, else create it."""
    gh = ctx.gh
    issue = next((i for i in gh.paginate(f"/repos/{gh.repo}/issues", state="open")
                  if i.get("title") == TITLE and "pull_request" not in i
                  and ((i.get("user") or {}).get("login") or "").lower() == ctx.bot.lower()), None)
    if issue:
        gh.edit_issue(issue["number"], body)
    else:
        gh.create_issue(TITLE, body)
    return body
