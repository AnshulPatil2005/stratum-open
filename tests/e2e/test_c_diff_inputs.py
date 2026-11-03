"""C. Diffs and GitHub inputs: whatever GitHub sends, the review completes and
posts only inside the diff."""

from __future__ import annotations

import json
import time

import pytest

from stratum_open.diff import parse_diff
from stratum_open.github import publish
from stratum_open.github.api import GitHub, GitHubError
from stratum_open.runner import review_pull

import diffs
from conftest import DIFF, SQL_LINE, head_of
from contract import assert_check_completed, assert_contract, assert_idempotent, assert_inline_within_diff
from fake_model import Response, finding, json_reply

HEAD = head_of(7)


def published(fake_github, make_context, logs, number=7, **context):
    ctx = make_context(**context)
    outcome = review_pull(ctx, number)
    assert outcome.status == "published", outcome
    assert_contract(fake_github, number=number, head=fake_github.pulls[number]["head"]["sha"], logs=logs)
    return outcome, ctx


def test_empty_pr_with_no_changed_files(fake_github, make_context, logs):
    fake_github.add_pull(7, diff="")
    outcome, _ = published(fake_github, make_context, logs)
    assert outcome.findings == []
    assert "**Findings:** none." in fake_github.summary_comments(7)[0]["body"]


@pytest.mark.parametrize("diff", [
    diffs.modified("a.py", ["x = 1"], ["x = 2"]),
    diffs.modified("a.py", ["x = 1"], ["x = 2"], start=1),
    diffs.modified("a.py", ["last"], ["last changed"], start=400, no_newline=True),
], ids=["one-line", "line-1", "last-line-no-newline"])
def test_tiny_changes(fake_github, make_context, fake_model, logs, diff):
    fake_github.add_pull(7, diff=diff)
    [file] = parse_diff(diff)
    [(line, text)] = file.added()
    fake_model.queue(Response.ok(json_reply([finding("a.py", text, line=line)])))
    outcome, _ = published(fake_github, make_context, logs)
    assert [c["line"] for c in fake_github.inline_comments(7)] == [line]


@pytest.mark.slow
def test_three_thousand_files(fake_github, make_context, logs):
    fake_github.add_pull(7, diff=diffs.many_files(3000))
    started = time.monotonic()
    outcome, _ = published(fake_github, make_context, logs)
    assert time.monotonic() - started < 60
    body = fake_github.summary_comments(7)[0]["body"]
    assert "Not read by the model" in body and "The checks ran on all 3000 files changed." in body


@pytest.mark.slow
def test_one_file_with_fifty_thousand_lines_is_clipped_not_dropped(fake_github, make_context, logs):
    fake_github.add_pull(7, diff=diffs.one_huge_file(50_000))
    outcome, _ = published(fake_github, make_context, logs)
    assert outcome.review.packed.clipped == ["big/generated.py"]
    assert "Read in part" in fake_github.summary_comments(7)[0]["body"]


def test_github_refuses_the_diff_so_the_file_patches_stand_in(fake_github, make_context, logs):
    files = [
        {"filename": "src/a.py", "status": "modified", "patch": "@@ -1,1 +1,2 @@\n x = 1\n+y = eval(input())", "additions": 1, "deletions": 0},
        {"filename": "dist/big.min.js", "status": "modified", "additions": 1, "deletions": 1},  # no patch: GitHub leaves it out past 1 MB
        {"filename": "img/logo.png", "status": "added", "additions": 0, "deletions": 0},
        {"filename": "old.py", "status": "removed", "patch": "@@ -1,1 +0,0 @@\n-gone = 1", "additions": 0, "deletions": 1},
        {"filename": "new_name.py", "previous_filename": "old_name.py", "status": "renamed", "additions": 0, "deletions": 0},
    ]
    fake_github.add_pull(7, diff=None, files=files)
    outcome, _ = published(fake_github, make_context, logs)
    assert {f.rule for f in outcome.findings} >= {"code/eval"}
    assert any(r["accept"].endswith("diff") for r in fake_github.requests_to("GET", r"/pulls/7$"))
    assert fake_github.requests_to("GET", r"/pulls/7/files")


@pytest.mark.parametrize("diff", [
    diffs.binary("img/logo.png", new=True),
    diffs.binary("img/logo.png"),
    diffs.deleted("old.py", ["gone = 1", "also = 2"]),
    diffs.renamed("a.py", "b.py"),
    diffs.renamed("a.py", "b.py", edits=(["x = 1"], ["x = 2"])),
    diffs.copied("a.py", "c.py"),
    diffs.mode_only("run.sh"),
    diffs.symlink("link", "target/file"),
    diffs.submodule("vendor/lib"),
    diffs.added("\"weird name \\303\\251.py\"", ["x = 1"]),
    diffs.added("\"bad escape \\q.py\"", ["x = 1"]),
], ids=["binary-new", "binary-modified", "deleted", "renamed", "renamed-edited", "copied", "mode-only", "symlink", "submodule", "quoted-path", "bad-escape-path"])
def test_unusual_file_changes(fake_github, make_context, fake_model, logs, diff):
    fake_github.add_pull(7, diff=diff + DIFF)
    fake_model.default = Response.ok(json_reply([finding("app/users.py", SQL_LINE, line=5), finding("img/logo.png", "x", line=1, title="Binary"),
                                                 finding("old.py", "gone = 1", line=1, title="Deleted"), finding("vendor/lib", "Subproject commit", line=1, title="Sub")]))
    outcome, _ = published(fake_github, make_context, logs)
    paths = {c["path"] for c in fake_github.inline_comments(7)}
    assert paths <= {"app/users.py", "b.py"}  # nothing on a binary, deleted or submodule entry


@pytest.mark.parametrize("line", [
    "s = 'héllo wörld ✓'",
    "s = 'emoji 🎉 and family 👨\u200d👩\u200d👧'",
    "s = 'RTL \u0627\u0644\u0633\u0644\u0627\u0645 text'",
    "if user\u200b == admin:  # zero-width space",
    "\ufeffimport os  # BOM mid-file",
    "x = 1 \u202eevil\u202c",
    "b = 'caf\xe9'\r",
], ids=["accented", "emoji", "rtl", "zero-width", "bom", "bidi", "crlf"])
def test_non_ascii_and_hidden_unicode_in_added_lines(fake_github, make_context, fake_model, logs, line):
    diff = diffs.modified("app/text.py", ["x = 0"], ["x = 0", line], start=1)
    fake_github.add_pull(7, diff=diff)
    fake_model.queue(Response.ok(json_reply([finding("app/text.py", line.rstrip("\r"), line=2, title="Odd text ✓", detail="détail 🎉")])))
    outcome, _ = published(fake_github, make_context, logs)
    hidden = [f for f in outcome.findings if f.rule == "unicode/hidden"]
    assert bool(hidden) is any(ch in line for ch in ("\u200b", "\ufeff", "\u202e"))
    for found in hidden:
        assert found.line_start == 2


def test_mixed_and_crlf_line_endings_keep_their_numbers(fake_github, make_context, fake_model, logs):
    diff = "diff --git a/w.py b/w.py\r\n--- a/w.py\r\n+++ b/w.py\r\n@@ -1,2 +1,3 @@\r\n a = 1\r\n+b = 2\r\n c = 3\n+d = eval(x)\r\n"
    fake_github.add_pull(7, diff=diff)
    outcome, _ = published(fake_github, make_context, logs)
    assert [(f.rule, f.line_start) for f in outcome.findings if f.rule == "code/eval"] == [("code/eval", 4)]


@pytest.mark.slow
def test_a_one_megabyte_minified_line_is_handled_in_bounded_time(fake_github, make_context, logs):
    fake_github.add_pull(7, diff=diffs.long_line(1_000_000))
    started = time.monotonic()
    published(fake_github, make_context, logs)
    assert time.monotonic() - started < 20


def test_closed_and_merged_prs_are_skipped(fake_github, make_context, fake_model):
    fake_github.add_pull(7, diff=DIFF, state="closed")
    fake_github.add_pull(8, diff=DIFF, state="closed", merged=True)
    for number in (7, 8):
        outcome = review_pull(make_context(), number)
        assert outcome.status == "skipped" and outcome.note == "the PR isn't open"
    assert fake_model.chat_calls == [] and fake_github.check_runs == []


def test_draft_pr_is_reviewed_without_a_label_when_asked_directly(fake_github, make_context, logs):
    fake_github.add_pull(7, diff=DIFF, draft=True)
    outcome, _ = published(fake_github, make_context, logs)
    assert fake_github.labels_on(7) == []


def test_pr_from_a_deleted_fork_and_a_deleted_author(fake_github, make_context, logs):
    fake_github.add_pull(7, diff=DIFF, fork=True, fork_deleted=True, author=None, body=None)
    outcome, _ = published(fake_github, make_context, logs)
    assert outcome.triage is not None


def test_force_push_while_the_review_runs_is_not_published(fake_github, make_context, fake_model, logs):
    fake_github.add_pull(7, diff=DIFF)

    def move(record):  # a new commit lands while Stratum is reading the diff
        if record["path"].endswith("/pulls/7") and "diff" in record["accept"]:
            fake_github.pulls[7]["head"]["sha"] = "c" * 40

    fake_github.on_request = move
    outcome = review_pull(make_context(), 7)
    assert outcome.status == "superseded"
    assert fake_github.summary_comments(7) == [] and fake_github.check_runs == [] and fake_github.inline_comments(7) == []


def test_hundreds_of_existing_comments_are_paged_through(fake_github, make_context, fake_model, logs):
    fake_github.add_pull(7, diff=DIFF)
    for n in range(250):
        fake_github.issue_comments[7].append({"id": 10_000 + n, "body": f"comment {n}", "user": {"login": "someone", "type": "User"}})
    old_state = publish.State(head="0" * 40, findings=[], ignored=[], ignored_rules=["code/sql"]).encode()
    fake_github.issue_comments[7].append({"id": 99_999, "body": publish.SUMMARY_MARKER + "\n### Stratum review\nold\n" + old_state,
                                          "user": {"login": fake_github.bot, "type": "Bot"}})
    fake_github.compare[("0" * 40, HEAD)] = None  # histories don't connect: review everything again
    outcome, _ = published(fake_github, make_context, logs)
    assert len(fake_github.requests_to("GET", r"/issues/7/comments")) >= 3
    assert "code/sql" not in {f.rule for f in outcome.findings}, "the ignore recorded in the old summary must be honoured"
    assert len(fake_github.summary_comments(7)) == 1 and fake_github.summary_comments(7)[0]["id"] == 99_999


def test_incremental_review_reads_only_the_changed_file_and_keeps_findings(fake_github, make_context, fake_model, logs):
    other = diffs.modified("app/other.py", ["o = 1"], ["o = 2"])
    fake_github.add_pull(7, diff=DIFF + other)
    fake_model.queue(Response.ok(json_reply([finding("app/users.py", SQL_LINE, line=5), finding("app/users.py", 'print("unreachable")', title="Dead code", detail="Never runs.", severity="medium")])))
    outcome, ctx = published(fake_github, make_context, logs)
    assert {f.title for f in outcome.findings if f.source == "model"} == {"Unknown id", "Dead code"}

    fixed = DIFF.replace(f"+    {SQL_LINE}", '+    cur = conn.execute("SELECT * FROM users WHERE id = ?", (user_id,))')
    fake_github.move_head(7, "b" * 40, fixed + other, changed=["app/users.py"])
    fake_model.queue(Response.ok(json_reply([])))  # the model forgets everything this time
    second = review_pull(ctx, 7)
    system, user = fake_model.prompts()[-1]
    assert "app/users.py" in user and "## app/other.py" not in user, "only the changed file is sent"
    titles = {f.title for f in second.findings if f.source == "model"}
    assert "Dead code" in titles and "Unknown id" not in titles
    assert_contract(fake_github, number=7, head="b" * 40, logs=logs)
    assert_idempotent(fake_github, 7, lambda: review_pull(ctx, 7))
    assert len(fake_github.inline_comments(7)) == 3  # the two model findings and the SQL check, posted once each


@pytest.mark.parametrize("retry_after", ["1", None, "Thu, 01 Jan 2026 00:00:00 GMT"], ids=["seconds", "absent", "http-date"])
def test_a_transient_github_rate_limit_is_retried(fake_github, make_context, logs, retry_after):
    fake_github.add_pull(7, diff=DIFF)
    fake_github.rate_limit(retry_after=retry_after, times=1)
    outcome, _ = published(fake_github, make_context, logs)


@pytest.mark.parametrize("status", [403, 404, 500, 502])
def test_github_refusing_the_pr_itself_is_an_error_not_a_traceback(fake_github, make_context, status):
    fake_github.add_pull(7, diff=DIFF)
    fake_github.fail("GET", r"/pulls/7$", status, times=0)
    with pytest.raises(GitHubError) as caught:
        review_pull(make_context(), 7)
    assert caught.value.status == status
    assert fake_github.check_runs == []  # nothing to attach a check to; the entry points report it


def test_a_422_on_a_comment_position_sinks_only_that_comment(fake_github, make_context, fake_model, logs):
    fake_github.add_pull(7, diff=DIFF)
    fake_github.fail("POST", r"/pulls/7/reviews", 422, body={"message": "Validation Failed", "errors": [{"message": "Line could not be resolved"}]}, times=1)
    fake_model.queue(Response.ok(json_reply([finding("app/users.py", SQL_LINE, line=5), finding("app/users.py", 'print("unreachable")', title="Dead", detail="d", severity="medium")])))
    outcome, _ = published(fake_github, make_context, logs)
    assert len(fake_github.requests_to("POST", r"/pulls/7/reviews")) >= 3  # the batch, then one by one
    assert len(fake_github.inline_comments(7)) >= 2


def test_github_5xx_on_posting_comments_still_completes_the_check_run(fake_github, make_context, logs):
    fake_github.add_pull(7, diff=DIFF)
    fake_github.fail("POST", r"/pulls/7/reviews", 502, times=0)
    outcome, _ = published(fake_github, make_context, logs)
    body = fake_github.summary_comments(7)[0]["body"]
    assert "Couldn't post inline comments (GitHub answered 502)" in body
    assert "SQL built from strings" in body, "the finding is listed in the summary instead"


def test_lost_permission_to_write_checks_does_not_crash(fake_github, make_context, logs):
    fake_github.add_pull(7, diff=DIFF)
    fake_github.fail("POST", r"/check-runs", 403, body={"message": "Resource not accessible by integration"}, times=0)
    outcome = review_pull(make_context(), 7)
    assert outcome.status == "published" and fake_github.summary_comments(7)
    assert any("couldn't publish the check run" in line for line in logs)
    assert_contract(fake_github, number=7, head=HEAD, logs=logs, check_run=False)


def test_lost_permission_to_comment_still_posts_the_check_run(fake_github, make_context, logs):
    fake_github.add_pull(7, diff=DIFF)
    fake_github.fail("POST", r"/issues/7/comments", 403, times=0)
    fake_github.fail("POST", r"/pulls/7/reviews", 403, times=0)
    outcome, _ = published(fake_github, make_context, logs)
    [run] = assert_check_completed(fake_github, HEAD)
    assert run["conclusion"] == "neutral" and "SQL built from strings" in json.dumps(run["output"])


def test_a_proxy_html_page_with_200_from_github_is_an_error_not_a_traceback(fake_github, make_context):
    fake_github.add_pull(7, diff=DIFF)
    fake_github.fail("GET", r"/pulls/7$", 200, text="<html><body>Service temporarily unavailable</body></html>", times=0)
    with pytest.raises(GitHubError) as caught:
        review_pull(make_context(), 7)
    assert "wasn't JSON" in str(caught.value)


def test_github_lists_a_pr_with_three_hundred_commits(fake_github, make_context, logs):
    fake_github.add_pull(7, diff=DIFF, commits=320)
    fake_github.pr_commits[7] = [{"sha": f"{n:040x}", "commit": {"message": f"c{n}", "author": {"email": "a@b.c"}}} for n in range(320)]
    published(fake_github, make_context, logs)


def test_the_compare_api_capping_at_300_files_falls_back_to_a_full_review(fake_github, make_context, fake_model, logs):
    fake_github.add_pull(7, diff=DIFF)
    outcome, ctx = published(fake_github, make_context, logs)
    fake_github.move_head(7, "b" * 40, DIFF, changed=[f"f{n}.py" for n in range(300)])
    second = review_pull(ctx, 7)
    assert len(fake_model.chat_calls) == 2 and "app/users.py" in fake_model.prompts()[-1][1]
    assert_contract(fake_github, number=7, head="b" * 40, logs=logs)


def test_damaged_state_from_an_older_release_is_skipped_not_fatal(fake_github, make_context, fake_model, logs):
    """The hidden state in the summary comment is Stratum's own, but an older
    release may have written fields with other types; a text line number must
    not raise when the finding is carried over."""
    fake_github.add_pull(7, diff=DIFF)
    stale = {"title": "Dead code", "detail": "Never runs.", "severity": 7, "category": None, "path": "app/users.py", "line_start": "7", "line_end": None,
             "source": "model", "rule": "model:dead-code", "fingerprint": "abcdefabcdef", "snippet": 'print("unreachable")', "confidence": "high", "needs_human": "yes"}
    broken = {"title": None, "detail": 5, "path": "app/users.py", "source": "model", "fingerprint": "000000000000", "line_start": [1], "snippet": "x"}
    state = publish.State(head="0" * 40, findings=[stale, broken, "not a dict", None], ignored=[], ignored_rules=[], triage={"key": "ready", "score": "n/a"})
    fake_github.issue_comments[7].append({"id": 99_999, "body": publish.SUMMARY_MARKER + "\nold\n" + state.encode(), "user": {"login": fake_github.bot, "type": "Bot"}})
    fake_github.compare[("0" * 40, HEAD)] = [{"filename": "app/other.py", "status": "modified"}]  # nothing the model needs to re-read
    fake_model.queue(Response.ok(json_reply([])))
    outcome, _ = published(fake_github, make_context, logs)
    carried = [f for f in outcome.findings if f.title == "Dead code"]
    assert carried and carried[0].line_start == 7 and carried[0].severity == "medium" and carried[0].needs_human is True
