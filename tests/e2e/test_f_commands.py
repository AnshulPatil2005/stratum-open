"""F. `/stratum` commands: every command at every permission level, odd
comments, and spam. `@stratum` isn't a command."""

from __future__ import annotations

import itertools
import subprocess
from dataclasses import replace

import pytest

from stratum_open.github import publish
from stratum_open.github.commands import HELP, handle, parse
from stratum_open.runner import review_pull

from conftest import DIFF, SQL_LINE, head_of
from contract import assert_contract, assert_no_secrets
from fake_model import Response, finding, json_reply

HEAD = head_of(7)
WRITERS = {"mona": "write", "max": "maintain", "ada": "admin"}
READERS = {"erin": "read", "tess": "triage"}


_comment_ids = itertools.count(555)


def event(body, *, login="erin", kind="User", inline_reply_to=None, action="created", number=7, issue=False):
    comment = {"id": next(_comment_ids), "body": body, "user": {"login": login, "type": kind} if login else None}
    if inline_reply_to:
        comment["in_reply_to_id"] = inline_reply_to
        return "pull_request_review_comment", {"action": action, "comment": comment, "pull_request": {"number": number}}
    issue_data = {"number": number} if issue else {"number": number, "pull_request": {}}
    return "issue_comment", {"action": action, "comment": comment, "issue": issue_data}


@pytest.fixture
def reviewed(fake_github, make_context, fake_model, logs):
    fake_github.add_pull(7, diff=DIFF)
    fake_github.permissions.update(WRITERS | READERS)
    fake_model.default = Response.ok(json_reply([finding("app/users.py", SQL_LINE, line=5), finding("app/users.py", 'print("unreachable")', title="Dead code", detail="Never runs.", severity="medium")]))
    ctx = make_context()
    review_pull(ctx, 7)
    return ctx


def replies(fake_github):
    """Every reply Stratum made, oldest first (ids rise with time across both kinds of comment)."""
    issue = [c for c in fake_github.issue_comments[7] if publish.SUMMARY_MARKER not in c["body"]]
    inline = [c for c in fake_github.review_comments if c.get("in_reply_to_id")]
    return [c["body"] for c in sorted(issue + inline, key=lambda c: c["id"])]


@pytest.mark.parametrize("command", ["review", "ignore model:dead-code", "mute code/sql", "unmute code/sql"])
@pytest.mark.parametrize("login", sorted(WRITERS) + sorted(READERS) + ["nobody"])
def test_write_commands_at_every_permission_level(reviewed, fake_github, fake_model, login, command):
    before = len(fake_model.chat_calls)
    done = handle(reviewed, *event(f"/stratum {command}", login=login))
    name = command.split()[0]
    if login in WRITERS:
        assert done == name
        if name == "review":
            assert len(fake_model.chat_calls) == before + 1
        if name in ("mute", "unmute"):
            assert "needs somewhere to store it" in replies(fake_github)[-1]  # App-only, said plainly
    else:
        assert done == "refused" and "only people with write access" in replies(fake_github)[-1]
        assert len(fake_model.chat_calls) == before
    assert_contract(fake_github, number=7, head=HEAD)


@pytest.mark.parametrize("command", ["explain", "triage", "record", "help", "wat"])
@pytest.mark.parametrize("login", ["mona", "carol", "erin", "nobody"])
def test_anyone_commands(reviewed, fake_github, login, command):
    done = handle(reviewed, *event(f"/stratum {command}", login=login))
    last = replies(fake_github)[-1]
    if command == "explain" and login not in WRITERS and login != "carol":
        # `explain` runs the model, so by default it's for the PR's author (carol) and people with write access.
        assert done == "refused" and "that's for this PR's author and people with write access" in last
        return
    assert done == {"wat": "help", "help": "help"}.get(command, command)
    if command == "explain":
        assert "Reply `/stratum explain` to one of Stratum's inline comments" in last
    elif command == "triage":
        assert "This PR is sorted as" in last
    elif command == "record":
        assert "needs the repo's history" in last
    else:
        assert last == HELP


def test_explain_and_ignore_as_replies_to_an_inline_comment(reviewed, fake_github, fake_model):
    dead = next(c for c in fake_github.inline_comments(7) if "Dead code" in c["body"])
    fake_github.files[("app/users.py", HEAD)] = "import sqlite3\n" * 10
    fake_model.queue(Response.ok("It never runs because the line above returns. Ask @alice."))
    assert handle(reviewed, *event("/stratum explain", login="carol", inline_reply_to=dead["id"])) == "explain"  # carol wrote the PR
    explained = replies(fake_github)[-1]
    assert explained.startswith("It never runs") and "`@alice`" in explained and "Explained by" in explained
    assert handle(reviewed, *event("/stratum ignore", login="mona", inline_reply_to=dead["id"])) == "ignore"
    state = publish.State.decode(fake_github.summary_comments(7)[0]["body"])
    assert len(state.ignored) == 1
    assert handle(reviewed, *event("/stratum ignore", login="mona")) == "ignore"
    assert "Reply `/stratum ignore` to one of Stratum's inline comments, or name a rule" in replies(fake_github)[-1]


def test_ignore_an_unknown_rule_is_recorded_harmlessly(reviewed, fake_github):
    assert handle(reviewed, *event("/stratum ignore no/such-rule", login="mona")) == "ignore"
    assert publish.State.decode(fake_github.summary_comments(7)[0]["body"]).ignored_rules == ["no/such-rule"]
    assert_contract(fake_github, number=7, head=HEAD)


def test_ignore_and_explain_on_a_comment_that_is_not_stratums(reviewed, fake_github):
    fake_github.review_comments.append({"id": 4242, "pull_request_number": 7, "body": "nice", "path": "app/users.py", "line": 5, "user": {"login": "erin", "type": "User"}})
    handle(reviewed, *event("/stratum ignore", login="mona", inline_reply_to=4242))
    assert "isn't one of Stratum's comments" in replies(fake_github)[-1]
    handle(reviewed, *event("/stratum explain", login="carol", inline_reply_to=4242))
    assert "isn't one of Stratum's comments" in replies(fake_github)[-1]


@pytest.mark.parametrize("body, kwargs, expected", [
    ("/stratum review", {"action": "edited"}, None),
    ("/stratum review", {"action": "deleted"}, None),
    ("/stratum review", {"login": "dependabot[bot]", "kind": "Bot"}, None),
    ("/stratum review", {"login": "stratum[bot]"}, None),
    ("/stratum review", {"login": None}, None),
    ("/stratum triage", {"issue": True}, None),
    ("> /stratum review\n\nquoting someone", {}, None),
    ("thanks\n   /STRATUM    TRIAGE   please, and /stratum record", {"login": "erin"}, "triage"),
    ("/stratum\ttriage", {}, None),
    ("email me at hi@stratum.dev", {}, None),
    ("Moved to https://github.com/stratum-org/stratum-open\n/stratum-org/stratum-open#53", {}, None),
    ("ok\r\n  /STRATUM  triage", {"login": "erin"}, "triage"),
    ("@stratum review", {"login": "mona"}, None),
    ("@Stratum triage", {"login": "erin"}, None),
], ids=["edited", "deleted", "bot", "self", "deleted-user", "issue-not-pr", "quoted", "whitespace-casing-trailing-two-commands", "tab", "email",
        "links", "crlf", "at-form", "at-form-anyone"])
def test_comments_that_must_not_run_commands(reviewed, fake_github, fake_model, body, kwargs, expected):
    before = len(replies(fake_github))
    assert handle(reviewed, *event(body, **kwargs)) == expected
    assert (len(replies(fake_github)) - before) == (1 if expected else 0)
    assert len(fake_model.chat_calls) == 1


def test_malformed_comment_payloads_do_not_crash(reviewed):
    assert handle(reviewed, "issue_comment", {"action": "created"}) is None
    assert handle(reviewed, "issue_comment", {"action": "created", "comment": None}) is None
    assert handle(reviewed, "issue_comment", {"action": "created", "comment": {"id": 1, "body": None, "user": None}, "issue": {"pull_request": {}}}) is None
    assert handle(reviewed, "pull_request_review_comment", {"action": "created", "comment": {"id": 1, "body": "/stratum triage", "user": {"login": "e"}}}) is None


def test_command_spam_runs_nothing_twice(reviewed, fake_github, fake_model):
    for n in range(50):
        handle(reviewed, *event(f"/stratum triage  #{n}", login="erin"))
    assert len(fake_model.chat_calls) == 1
    assert sum("This PR is sorted as" in r for r in replies(fake_github)) == 50
    for _ in range(5):
        handle(reviewed, *event("/stratum review", login="mona"))
    assert len(fake_github.summary_comments(7)) == 1, "re-reviews edit the one summary"
    assert len({(c["path"], c["line"], c["body"].split("\n")[0]) for c in fake_github.inline_comments(7)}) == len(fake_github.inline_comments(7))


def test_permission_lookup_failing_refuses_politely(reviewed, fake_github, fake_model):
    fake_github.fail("GET", r"/collaborators/.*/permission", 502, times=0)
    assert handle(reviewed, *event("/stratum review", login="mona")) == "refused"
    assert "couldn't check your access" in replies(fake_github)[-1]
    assert len(fake_model.chat_calls) == 1


def test_record_for_an_author_with_no_history(fake_github, make_context, tmp_path):
    repo = tmp_path / "repo"
    repo.mkdir()
    subprocess.run(["git", "-C", str(repo), "init", "-q"], check=True)
    fake_github.add_pull(7, diff=DIFF)
    ctx = make_context(checkout=str(repo), model=None)
    handle(ctx, *event("/stratum record", login="erin"))
    assert "No merged PRs found" in fake_github.issue_comments[7][-1]["body"]  # an empty repo has history, just none of theirs
    assert_no_secrets(fake_github)
    handle(ctx, *event("/stratum record", login="mallory"))
    ctx.policy = replace(ctx.policy, track_record=replace(ctx.policy.track_record, exclude=("erin",)))
    handle(ctx, *event("/stratum record", login="erin"))
    assert "excludes your login" in fake_github.issue_comments[7][-1]["body"]


def test_parse_handles_every_spelling():
    assert parse("/Stratum Re-Review now") == ("re-review", "now")
    assert parse("@Stratum Re-Review now") is None
    assert parse("") is None and parse(None) is None  # type: ignore[arg-type]
    assert parse("@stratum") is None and parse("/stratum") is None


@pytest.mark.parametrize("body, expected", [
    # The table in issue #53: GitHub renders the first three as a mention of
    # the unrelated `stratum` organization, so only `/stratum` is a command.
    ("@stratum ignore", None),
    ("@Stratum ignore", None),
    ("\\@stratum ignore", None),
    ("`@stratum ignore`", None),
    ("/stratum ignore", ("ignore", "")),
    ("@stratum-open ignore", None),
    ("/stratum-open ignore", None),
    # Links and paths aren't commands.
    ("/stratum-org/stratum-open", None),
    ("https://github.com/stratum-org/stratum-open/issues/53", None),
    ("see github.com/stratum-org/stratum-open", None),
    ("/stratum/review", None),
    ("//stratum review", None),
    ("cd /stratum review", None),
    ("`/stratum review`", None),
    ("thanks!\n  /stratum ignore workflow/unpinned-action\n", ("ignore", "workflow/unpinned-action")),
])
def test_parse_reads_a_slash_command_and_nothing_else(body, expected):
    assert parse(body) == expected


@pytest.mark.parametrize("body, expected", [
    ("\u00a0/stratum review", None),
    ("\r/stratum review", None),
    ("\v/stratum review", None),
    ("\u2028/stratum review", None),
    ("\u3000/stratum review", None),
    (" \t /stratum review", ("review", "")),
])
def test_only_spaces_and_tabs_may_come_before_a_command(body, expected):
    assert parse(body) == expected


@pytest.mark.parametrize("name", ["review", "re-review", "rereview", "ignore", "mute", "unmute", "explain", "triage", "record", "help"])
def test_every_command_is_read_only_as_slash(name):
    assert parse(f"/stratum {name} x") == (name, "x")
    assert parse(f"@stratum {name} x") is None


def test_stratum_shows_commands_as_slash_and_its_own_text_is_never_one(reviewed, fake_github):
    handle(reviewed, *event("/stratum help"))
    assert replies(fake_github)[-1] == HELP
    inline = [c["body"] for c in fake_github.inline_comments(7)]
    [summary] = [c["body"] for c in fake_github.summary_comments(7)]
    assert inline and all("Reply `/stratum ignore` to hide it on this PR, or `/stratum explain` for more." in body for body in inline)
    assert "Comment `/stratum help` for commands." in summary
    for body in [HELP, summary, *inline]:
        assert "@stratum" not in body
        assert parse(body) is None, "Stratum's own text must never read as a command"
