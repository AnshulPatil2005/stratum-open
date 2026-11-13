"""F. `/stratum ask <question>` end to end: the fake GitHub and the fake model
server, in process, from the Action and from the App."""

from __future__ import annotations

import json
import time

import pytest
from conftest import DIFF, SQL_LINE, head_of
from contract import assert_contract, assert_no_traceback, run_stratum
from fake_model import Response, finding, json_reply
from test_f_commands import READERS, WRITERS, event, replies

from stratum_open.github import publish
from stratum_open.github.commands import handle, parse
from stratum_open.runner import review_pull

HEAD = head_of(7)


@pytest.fixture
def reviewed(fake_github, make_context, fake_model, logs):
    """The PR reviewed once, with two findings: test_f_commands' starting point."""
    fake_github.add_pull(7, diff=DIFF)
    fake_github.permissions.update(WRITERS | READERS)
    fake_model.default = Response.ok(json_reply([finding("app/users.py", SQL_LINE, line=5), finding("app/users.py", 'print("unreachable")', title="Dead code", detail="Never runs.", severity="medium")]))
    ctx = make_context()
    review_pull(ctx, 7)
    return ctx


def test_a_question_on_the_pr_is_answered_from_its_diff_and_review(reviewed, fake_github, fake_model):
    before = len(fake_model.chat_calls)
    fake_model.queue(Response.ok("`get_user` builds the SQL with an f-string, so `user_id` reaches the query as text."))
    assert handle(reviewed, *event("/stratum ask is get_user safe with a string id?", login="carol")) == "ask"
    assert len(fake_model.chat_calls) == before + 1
    system, user = fake_model.prompts()[-1]
    assert "say so plainly" in system
    assert "<question>\nis get_user safe with a string id?\n</question>" in user
    assert SQL_LINE in user and "Unknown id" in user and "Dead code" in user
    answer = replies(fake_github)[-1]
    assert answer.startswith("`get_user` builds the SQL") and "Answered by `fake-7b`" in answer
    assert answer.endswith("<!-- stratum:asked carol -->")
    assert len(fake_github.summary_comments(7)) == 1
    assert_contract(fake_github, number=7, head=HEAD)


def test_a_question_replying_to_an_inline_comment_is_answered_in_its_thread(reviewed, fake_github, fake_model):
    dead = next(c for c in fake_github.inline_comments(7) if "Dead code" in c["body"])
    fake_github.files[("app/users.py", HEAD)] = "import sqlite3\n" * 10
    fake_model.queue(Response.ok("It never runs: the line before it returns."))
    assert handle(reviewed, *event("/stratum ask why does it never run?", login="carol", inline_reply_to=dead["id"])) == "ask"
    user = fake_model.prompts()[-1][1]
    assert "The question replies to this inline comment on app/users.py" in user and "Never runs." in user
    reply = fake_github.review_comments[-1]
    assert reply["in_reply_to_id"] == dead["id"] and reply["body"].startswith("It never runs")


def test_an_answer_that_pings_or_commands_is_posted_harmlessly(reviewed, fake_github, fake_model):
    fake_model.queue(Response.ok("Ask @alice. Or run:\n/stratum mute code/sql\n```suggestion\nreturn None\n```\n<!-- stratum:summary -->"))
    assert handle(reviewed, *event("/stratum ask who knows?", login="mona")) == "ask"
    answer = replies(fake_github)[-1]
    assert "`@alice`" in answer and parse(answer) is None
    assert "suggestion" not in answer and answer.count("<!--") == 1
    # The answer is the bot's, so handle() skips it; and it's never taken for the summary.
    assert handle(reviewed, "issue_comment", {"action": "created", "comment": fake_github.issue_comments[7][-1],
                                             "issue": {"number": 7, "pull_request": {}}}) is None
    assert len(fake_github.summary_comments(7)) == 1


def test_a_model_error_is_one_plain_line(reviewed, fake_github, fake_model):
    fake_model.queue(Response.status(503), Response.status(503), Response.status(503))
    assert handle(reviewed, *event("/stratum ask why?", login="carol")) == "ask"
    answer = replies(fake_github)[-1]
    assert answer.startswith("The model couldn't answer just now (") and "Try again later." in answer
    assert_no_traceback(answer)


@pytest.mark.parametrize("login, done", [("carol", "ask"), ("mona", "ask"), ("erin", "refused"), ("nobody", "refused")])
def test_who_may_ask(reviewed, fake_github, fake_model, login, done):
    before = len(fake_model.chat_calls)
    fake_model.queue(Response.ok("An answer."))
    assert handle(reviewed, *event("/stratum ask why?", login=login)) == done
    assert len(fake_model.chat_calls) == before + (done == "ask")


@pytest.mark.parametrize("body, says", [("/stratum ask", "Put the question after the command"),
                                        ("/stratum ask " + "x" * 1001, "Stratum takes up to 1000")])
def test_empty_and_long_questions_make_no_model_call(reviewed, fake_github, fake_model, body, says):
    before = len(fake_model.chat_calls)
    assert handle(reviewed, *event(body, login="carol")) == "ask"
    assert says in replies(fake_github)[-1] and len(fake_model.chat_calls) == before


def test_a_fenced_ask_is_shown_not_run(reviewed, fake_github, fake_model):
    before = len(replies(fake_github))
    assert handle(reviewed, *event("Try:\n```\n/stratum ask why?\n```", login="carol")) is None
    assert len(replies(fake_github)) == before


# ── the Action and the App ───────────────────────────────────────────────


def test_the_action_answers_a_comment(fake_github, fake_model, tmp_path):
    from test_cli_process import action_env, pr_payload

    fake_github.add_pull(7, diff=DIFF)
    fake_model.queue(Response.ok(json_reply([finding("app/users.py", SQL_LINE, line=5)])))
    run_stratum(["action"], env=action_env(fake_github, fake_model, tmp_path, pr_payload()))
    fake_model.queue(Response.ok("Because the id is formatted into the SQL."))
    comment = {"action": "created", "repository": {"default_branch": "main"}, "issue": {"number": 7, "pull_request": {}},
               "comment": {"id": 1, "body": "/stratum ask why is this high?", "user": {"login": "carol", "type": "User"}}}
    env = action_env(fake_github, fake_model, tmp_path, comment, event="issue_comment")
    result = run_stratum(["action"], env=env)
    assert result.returncode == 0 and "Command: ask" in result.stdout, result.stderr
    answer = fake_github.issue_comments[7][-1]["body"]
    assert answer.startswith("Because the id") and "Answered by `" in answer
    from stratum_open import action

    assert action.needs_model(env) is True  # so the workflow starts Ollama for it


def test_the_app_answers_a_comment(fake_github, fake_model, tmp_path, monkeypatch):
    try:
        from cryptography.hazmat.primitives import serialization
        from cryptography.hazmat.primitives.asymmetric import rsa
    except ImportError:  # pragma: no cover
        pytest.skip("cryptography (the `app` extra) isn't installed")
    from test_h_app import SECRET, call

    from stratum_open.app import AppAuth, Server
    from stratum_open.storage import SQLiteStorage

    key = rsa.generate_private_key(public_exponent=65537, key_size=2048).private_bytes(
        serialization.Encoding.PEM, serialization.PrivateFormat.TraditionalOpenSSL, serialization.NoEncryption()).decode()
    fake_github.add_pull(7, diff=DIFF)
    fake_model.queue(Response.ok("It formats the id into the SQL."))
    monkeypatch.setattr(Server, "checkout", lambda self, repo, token, branch, window_days: None)
    server = Server(AppAuth("1", key, api=fake_github.url), SECRET.decode(), SQLiteStorage(str(tmp_path / "s.db")), str(tmp_path / "repos"),
                    env={"STRATUM_MODEL_ENDPOINT": fake_model.url}, workers=1, log=lambda line: None)
    body = json.dumps({"action": "created", "installation": {"id": 1}, "repository": {"full_name": "acme/widget", "default_branch": "main"},
                       "issue": {"number": 7, "pull_request": {}},
                       "comment": {"id": 77, "body": "/stratum ask what does this change?", "user": {"login": "carol", "type": "User"}}}).encode()
    assert call(server, body, event="issue_comment") == ("202 Accepted", {"queued": True})
    deadline = time.monotonic() + 30
    while not any(job["event"] == "command" for job in server.storage.export("acme/widget")["jobs"]) and time.monotonic() < deadline:
        time.sleep(0.05)
    assert server.storage.export("acme/widget")["jobs"][-1]["outcome"] == "ask"
    [answer] = [c["body"] for c in fake_github.issue_comments[7] if publish.SUMMARY_MARKER not in c["body"]]
    assert answer.startswith("It formats the id") and answer.endswith("<!-- stratum:asked carol -->")
