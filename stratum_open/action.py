"""The GitHub Action: one run per PR event, nothing kept between runs.

The workflow checks out the **default branch** (never the PR) so Stratum can
read the project's history; the PR itself is only ever read through the API.
That's what makes `pull_request_target` safe here: no code from the PR runs.
"""

from __future__ import annotations

import json
import os
import subprocess
import sys
import traceback
from dataclasses import dataclass, replace
from typing import Mapping

import requests

from stratum_open.checks.dependencies import PackageLookup
from stratum_open.github import publish
from stratum_open.github.api import API, GitHub, GitHubError
from stratum_open.github.commands import NEEDS_MODEL, handle, parse
from stratum_open.model import model_from_policy
from stratum_open.policy import Policy
from stratum_open.runner import Context, checkout_ref, load_context_policy, review_pull, skip_reason
from stratum_open.storage import redact
from stratum_open.triage_queue import update_queue

DEFAULT_BOT = "github-actions[bot]"


def main(env: Mapping[str, str] = os.environ) -> int:
    """Exit 0 when the event was handled, 1 when it couldn't be. A failure is
    one `::error::` line for the job's log, never a traceback; when the event
    names a commit, a neutral check run says the review didn't finish, so
    nothing is left waiting on it."""
    event_name = env.get("GITHUB_EVENT_NAME", "")
    try:
        with open(env["GITHUB_EVENT_PATH"], encoding="utf-8") as handle_file:
            payload = json.load(handle_file)
    except (KeyError, OSError, ValueError) as exc:
        print(f"::error::Stratum couldn't read the event payload: {type(exc).__name__}: {exc}")
        return 1
    if not isinstance(payload, dict):
        print("::error::Stratum couldn't read the event payload: it isn't a JSON object.")
        return 1
    token = env.get("STRATUM_GITHUB_TOKEN") or env.get("GITHUB_TOKEN")
    if not token:
        print("::error::Stratum needs a GitHub token: pass github-token to the action.")
        return 1
    repo = env.get("GITHUB_REPOSITORY") or (payload.get("repository") or {}).get("full_name") or ""
    gh = GitHub(token, repo, api=env.get("GITHUB_API_URL") or API)
    loaded = Loaded()  # filled in by _run once the policy is read, so a failure check matches the policy's check name
    try:
        return _run(env, event_name, payload, gh, loaded)
    except (GitHubError, requests.RequestException) as exc:
        print(f"::error::Stratum couldn't finish: {_describe(exc)}")
        _fail_check(gh, payload, _describe(exc), loaded.check_name)
        return 1
    except Exception as exc:  # anything else is a bug: one line for the job, the details in the log
        print(f"::error::Stratum hit a bug and couldn't finish: {type(exc).__name__}: {exc}")
        print(redact(traceback.format_exc()), file=sys.stderr)
        _fail_check(gh, payload, f"Stratum hit a bug ({type(exc).__name__}) and couldn't finish this review.", loaded.check_name)
        return 1


@dataclass
class Loaded:
    """What `_run` has learned by the time it fails, for the failure path."""
    check_name: str = publish.CHECK_NAME


def _describe(exc: BaseException) -> str:
    if isinstance(exc, GitHubError):
        return str(exc)
    return f"couldn't reach GitHub ({type(exc).__name__})"


def _fail_check(gh: GitHub, payload: dict, message: str, name: str = publish.CHECK_NAME) -> None:
    """Best effort: a completed, neutral check run on the PR's head saying the
    review didn't happen, so nobody waits on a check that never comes. `name`
    is the policy's `review.check_name` when the policy was read, which is the
    name branch protection waits for."""
    head = ((payload.get("pull_request") or {}).get("head") or {}).get("sha")
    if not head or not gh.repo:
        return
    try:
        gh.check_run(head, name, "neutral", "Stratum couldn't finish", redact(message)[:1000], "", [])
    except (GitHubError, requests.RequestException) as exc:
        print(f"::warning::Stratum couldn't post its check run either: {_describe(exc)}")


def _run(env: Mapping[str, str], event_name: str, payload: dict, gh: GitHub, loaded: Loaded | None = None) -> int:
    default_branch = (payload.get("repository") or {}).get("default_branch") or gh.repository().get("default_branch") or "main"
    policy = context_from_env(load_context_policy(gh, default_branch), env)
    if loaded is not None:
        loaded.check_name = policy.review.check_name
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
        pull = payload.get("pull_request") or {}
        if not pull.get("number"):
            print("::error::The event has no pull request in it.")
            return 1
        reason = skip_reason(pull, payload.get("action", ""), policy)
        if reason:
            print(reason)
        else:
            outcome = review_pull(ctx, pull["number"])
            print(f"PR #{outcome.number}: {outcome.status}, {len(outcome.findings)} finding(s)" + (f", {outcome.triage.key}" if outcome.triage else ""))
    elif event_name in ("issue_comment", "pull_request_review_comment"):
        done = handle(ctx, event_name, payload)
        print(f"Command: {done or 'none in this comment'}")
    elif event_name == "workflow_run":
        # CI finished: sort the PR again with its result. Same head, so no model
        # call. The skip settings apply here too; review.events doesn't, since
        # this isn't a PR event.
        for ref in (payload.get("workflow_run") or {}).get("pull_requests") or []:
            if not isinstance(ref, dict) or not ref.get("number"):
                continue
            reason = skip_reason(gh.pull(ref["number"]), None, policy)
            if reason:
                print(f"PR #{ref['number']}: {reason}")
                continue
            outcome = review_pull(ctx, ref["number"])
            print(f"PR #{outcome.number}: re-sorted as {outcome.triage.key if outcome.triage else outcome.status}")
    elif event_name in ("schedule", "workflow_dispatch"):
        update_queue(ctx)
        print("Triage queue updated.")
    else:
        print(f"Stratum doesn't handle {event_name} events.")
    return 0


def context_from_env(policy: Policy, env: Mapping[str, str]) -> Policy:
    """STRATUM_CONTEXT_TOKENS, which the action sets when Ollama runs on the
    runner's CPU: a big prompt there takes longer to read than a model call
    may last, so calls stay small. The policy file's own value wins."""
    raw = (env.get("STRATUM_CONTEXT_TOKENS") or "").strip()
    if not raw.isdigit() or "review.context_tokens" in policy.explicit:
        return policy
    return replace(policy, review=replace(policy.review, context_tokens=max(2048, int(raw))),
                   explicit=policy.explicit | {"review.context_tokens"})


def needs_model(env: Mapping[str, str] = os.environ) -> bool:
    """Whether this event can call the model, so the workflow only starts
    Ollama when it has to. It asks the same parser that runs the command."""
    if env.get("GITHUB_EVENT_NAME") not in ("issue_comment", "pull_request_review_comment"):
        return True
    with open(env["GITHUB_EVENT_PATH"], encoding="utf-8") as handle_file:
        payload = json.load(handle_file)
    parsed = parse((payload.get("comment") or {}).get("body", ""))
    return parsed is not None and parsed[0] in NEEDS_MODEL


def _checkout(workspace: str | None) -> str | None:
    if not workspace:
        return None
    inside = subprocess.run(["git", "-C", workspace, "rev-parse", "--is-inside-work-tree"], capture_output=True, text=True)
    return workspace if inside.returncode == 0 and inside.stdout.strip() == "true" else None


if __name__ == "__main__":
    raise SystemExit(main())
