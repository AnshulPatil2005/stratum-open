"""The GitHub Action: one run per PR event, nothing kept between runs.

The workflow checks out the **default branch** (never the PR) so Stratum can
read the project's history; the PR itself is only ever read through the API.
That's what makes `pull_request_target` safe here: no code from the PR runs.
"""

from __future__ import annotations

import json
import os
import subprocess
from typing import Mapping

from stratum_open.checks.dependencies import PackageLookup
from stratum_open.github.api import API, GitHub
from stratum_open.github.commands import handle
from stratum_open.model import model_from_policy
from stratum_open.triage_queue import update_queue
from stratum_open.runner import Context, checkout_ref, load_context_policy, review_pull

REVIEW_ACTIONS = {"opened", "synchronize", "reopened", "ready_for_review"}
DEFAULT_BOT = "github-actions[bot]"


def main(env: Mapping[str, str] = os.environ) -> int:
    event_name = env["GITHUB_EVENT_NAME"]
    with open(env["GITHUB_EVENT_PATH"], encoding="utf-8") as handle_file:
        payload = json.load(handle_file)
    token = env.get("STRATUM_GITHUB_TOKEN") or env.get("GITHUB_TOKEN")
    if not token:
        print("::error::Stratum needs a GitHub token: pass github-token to the action.")
        return 1
    gh = GitHub(token, env["GITHUB_REPOSITORY"], api=env.get("GITHUB_API_URL", API))
    default_branch = (payload.get("repository") or {}).get("default_branch") or gh.repository()["default_branch"]
    policy = load_context_policy(gh, default_branch)
    for error in policy.errors:
        print(f"::warning::.stratum/policy.yml: {error} Using the defaults.")
    for warning in policy.warnings:
        print(f"::notice::.stratum/policy.yml: {warning}")

    checkout = _checkout(env.get("GITHUB_WORKSPACE"))
    ctx = Context(
        gh=gh,
        policy=policy,
        model=model_from_policy(policy, env),
        bot=env.get("STRATUM_BOT_LOGIN") or DEFAULT_BOT,
        checkout=checkout,
        history_ref=checkout_ref(checkout, default_branch) if checkout else "HEAD",
        lookup=PackageLookup(),
        own_checks=tuple(name for name in (env.get("GITHUB_JOB"), env.get("STRATUM_JOB_NAME")) if name),
    )

    if event_name in ("pull_request", "pull_request_target"):
        pull = payload["pull_request"]
        if payload.get("action") not in REVIEW_ACTIONS:
            print(f"Nothing to do for a {payload.get('action')} event.")
        elif pull.get("draft"):
            print("Draft PR: Stratum reviews it once it's ready for review, or on `@stratum review`.")
        else:
            outcome = review_pull(ctx, pull["number"])
            print(f"PR #{outcome.number}: {outcome.status}, {len(outcome.findings)} finding(s)" + (f", {outcome.triage.key}" if outcome.triage else ""))
    elif event_name in ("issue_comment", "pull_request_review_comment"):
        done = handle(ctx, event_name, payload)
        print(f"Command: {done or 'none in this comment'}")
    elif event_name == "workflow_run":
        # CI finished: sort the PR again with its result. Same head, so no model call.
        for pull in (payload.get("workflow_run") or {}).get("pull_requests") or []:
            outcome = review_pull(ctx, pull["number"])
            print(f"PR #{outcome.number}: re-sorted as {outcome.triage.key if outcome.triage else outcome.status}")
    elif event_name in ("schedule", "workflow_dispatch"):
        update_queue(ctx)
        print("Triage queue updated.")
    else:
        print(f"Stratum doesn't handle {event_name} events.")
    return 0


def _checkout(workspace: str | None) -> str | None:
    if not workspace:
        return None
    inside = subprocess.run(["git", "-C", workspace, "rev-parse", "--is-inside-work-tree"], capture_output=True, text=True)
    return workspace if inside.returncode == 0 and inside.stdout.strip() == "true" else None


if __name__ == "__main__":
    raise SystemExit(main())
