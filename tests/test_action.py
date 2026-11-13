import json
import re
from pathlib import Path

import pytest

from fakes import FakeGitHub, FakeModel
from test_runner import DIFF, model_answer

from stratum_open import action
from stratum_open.github.commands import parse


def run(tmp_path, monkeypatch, event_name, payload, gh, model=None):
    event = tmp_path / "event.json"
    event.write_text(json.dumps(payload))
    monkeypatch.setattr(action, "GitHub", lambda token, repo, api: gh)
    monkeypatch.setattr(action, "model_from_policy", lambda policy, env: model or FakeModel(model_answer()))
    env = {"GITHUB_EVENT_NAME": event_name, "GITHUB_EVENT_PATH": str(event), "GITHUB_REPOSITORY": "acme/widget",
           "STRATUM_GITHUB_TOKEN": "t", "STRATUM_BOT_LOGIN": "stratum[bot]"}
    return action.main(env)


def pr_payload(action_name="opened", draft=False):
    return {"action": action_name, "repository": {"default_branch": "main"}, "pull_request": {"number": 7, "draft": draft}}


def test_a_pr_event_reviews_the_pr(tmp_path, monkeypatch):
    gh = FakeGitHub(diff=DIFF)
    assert run(tmp_path, monkeypatch, "pull_request_target", pr_payload(), gh) == 0
    assert gh.issue_comments_[7] and gh.checks


def test_drafts_and_other_actions_are_skipped(tmp_path, monkeypatch, capsys):
    gh = FakeGitHub(diff=DIFF)
    run(tmp_path, monkeypatch, "pull_request_target", pr_payload(draft=True), gh)
    run(tmp_path, monkeypatch, "pull_request_target", pr_payload("labeled"), gh)
    assert gh.issue_comments_[7] == []
    out = capsys.readouterr().out
    assert "Draft PR" in out and "Nothing to do for a labeled event." in out


def test_comment_events_run_commands(tmp_path, monkeypatch):
    gh = FakeGitHub(diff=DIFF)
    payload = {"action": "created", "repository": {"default_branch": "main"}, "issue": {"number": 7, "pull_request": {}},
               "comment": {"id": 1, "body": "/stratum help", "user": {"login": "erin", "type": "User"}}}
    run(tmp_path, monkeypatch, "issue_comment", payload, gh)
    assert "Stratum commands" in gh.issue_comments_[7][-1]["body"]


def test_schedule_builds_the_queue(tmp_path, monkeypatch):
    gh = FakeGitHub(diff=DIFF)
    run(tmp_path, monkeypatch, "pull_request_target", pr_payload(), gh)
    run(tmp_path, monkeypatch, "schedule", {"repository": {"default_branch": "main"}}, gh)
    [queue] = gh.issues
    assert queue["title"] == "Stratum triage queue" and "#7 Add a thing" in queue["body"]


@pytest.mark.parametrize(
    "event_name, body, expected",
    [
        ("pull_request_target", None, True),
        ("schedule", None, True),
        ("issue_comment", "/stratum record", False),
        ("issue_comment", "/stratum  rereview", True),
        ("pull_request_review_comment", "/Stratum Ignore", True),
        ("issue_comment", "@stratum review", False),
        ("issue_comment", "/stratum triage", False),
        ("issue_comment", "/stratum review", True),
        ("pull_request_review_comment", "/Stratum explain", True),
        ("issue_comment", "/stratum ask why is this here?", True),
        ("pull_request_review_comment", "/stratum ask\nwhy?", True),
        ("issue_comment", "thanks!", False),
    ],
)
def test_needs_model_asks_the_command_parser(tmp_path, event_name, body, expected):
    event = tmp_path / "event.json"
    event.write_text(json.dumps({"comment": {"body": body}} if body is not None else {}))
    assert action.needs_model({"GITHUB_EVENT_NAME": event_name, "GITHUB_EVENT_PATH": str(event)}) is expected


def comment_filter(path: str) -> list[str]:
    """What the workflow's job filter looks for in a comment's body."""
    text = (Path(__file__).parent.parent / path).read_text(encoding="utf-8")
    return re.findall(r"contains\(github\.event\.comment\.body, '([^']*)'\)", text)


def starts_the_job(needles: list[str], body: str) -> bool:
    return any(needle.lower() in body.lower() for needle in needles)  # GitHub's contains() ignores case


@pytest.mark.parametrize("path, needles", [("examples/stratum.yml", ["/stratum "]), (".github/workflows/stratum.yml", ["/stratum "])])
def test_the_workflow_starts_a_job_for_every_comment_the_parser_reads_as_a_command(path, needles):
    assert comment_filter(path) == needles
    commands = ["/stratum review", "/Stratum REVIEW", "thanks!\n/stratum review", "/stratum review\n", "ok\r\n/stratum  ignore code/sql\r\n",
                "  /stratum record", "/stratum ask why is this here?", "/stratum ask\nwhy?"]
    for body in commands:
        assert parse(body) is not None and starts_the_job(needles, body), body
    # A slash command needs the space after it, the same text the filter looks for.
    for body in ["/stratum\treview", "/stratum\nreview"]:
        assert parse(body) is None and not starts_the_job(needles, body), body
    for body in ["@stratum review", "@Stratum\ttriage"]:
        assert parse(body) is None and starts_the_job(needles, body) is ("@stratum" in needles), body
    # Links to the project don't start a job.
    for body in ["Fixes https://github.com/stratum-org/stratum-open/issues/53", "/stratum-org/stratum-open", "see stratum-org/stratum-open#52"]:
        assert parse(body) is None and not starts_the_job(needles, body), body


def test_ci_finishing_respects_the_skip_settings(tmp_path, monkeypatch, capsys):
    from stratum_open import action as action_module

    gh = FakeGitHub(diff=DIFF, author="renovate[bot]")
    gh.files[(".stratum/policy.yml", "main")] = "review:\n  skip_bots: true\n"
    reviewed = []
    monkeypatch.setattr(action_module, "review_pull", lambda ctx, number: reviewed.append(number))
    payload = {"repository": {"default_branch": "main"}, "workflow_run": {"pull_requests": [{"number": 7}]}}
    run(tmp_path, monkeypatch, "workflow_run", payload, gh)
    assert reviewed == [] and "review.skip_bots is on" in capsys.readouterr().out


def test_the_runners_cpu_gets_small_calls_unless_the_policy_says_otherwise():
    from stratum_open.action import context_from_env
    from stratum_open.policy import parse_policy

    assert context_from_env(parse_policy(None), {"STRATUM_CONTEXT_TOKENS": "8192"}).review.context_tokens == 8192
    assert context_from_env(parse_policy(None), {}).review.context_tokens == 16384
    pinned = parse_policy("review:\n  context_tokens: 12000\n")
    assert context_from_env(pinned, {"STRATUM_CONTEXT_TOKENS": "8192"}).review.context_tokens == 12000
    # Explicit now, so the profile doesn't raise it again.
    assert "review.context_tokens" in context_from_env(parse_policy(None), {"STRATUM_CONTEXT_TOKENS": "8192"}).explicit
