import json

import pytest

from fakes import FakeGitHub, FakeModel
from test_runner import DIFF, model_answer

from stratum_open import action


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
               "comment": {"id": 1, "body": "@stratum help", "user": {"login": "erin", "type": "User"}}}
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
        ("issue_comment", "@stratum triage", False),
        ("issue_comment", "@stratum record", False),
        ("issue_comment", "@stratum  rereview", True),
        ("issue_comment", "@stratum\texplain", True),
        ("pull_request_review_comment", "@Stratum Ignore", True),
        ("issue_comment", "thanks!", False),
    ],
)
def test_needs_model_asks_the_command_parser(tmp_path, event_name, body, expected):
    event = tmp_path / "event.json"
    event.write_text(json.dumps({"comment": {"body": body}} if body is not None else {}))
    assert action.needs_model({"GITHUB_EVENT_NAME": event_name, "GITHUB_EVENT_PATH": str(event)}) is expected
