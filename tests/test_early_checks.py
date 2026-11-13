"""`review.early_checks`: the checks' findings go up before the model has
run, and the full review then completes them in place. The end state must be
the one a review posts with the switch off."""

import itertools
import json

import pytest
from fakes import FakeGitHub, FakeModel
from test_runner import DIFF, FIXED, model_answer

from stratum_open import action
from stratum_open.github import publish
from stratum_open.policy import parse_policy
from stratum_open.runner import Context, post_early, review_pull, stop_early

BOT = "stratum[bot]"
HEAD = "a" * 40
EARLY = "review:\n  early_checks: true\n"
_ids = itertools.count(50_000)


class GitHub(FakeGitHub):
    """FakeGitHub with check runs that can be started and completed later,
    and review comments that can be deleted."""

    def __init__(self, **kwargs):
        super().__init__(**kwargs)
        self.deleted: list[int] = []

    def check_run(self, head_sha, name, conclusion, title, summary, text, annotations, run_id=None):
        record = {"head": head_sha, "name": name, "status": "completed", "conclusion": conclusion, "title": title,
                  "summary": summary, "annotations": annotations}
        for run in self.checks:
            if run_id and run["id"] == run_id:
                run.update(record)
                return run
        self.checks.append({"id": next(_ids), **record})
        return self.checks[-1]

    def start_check_run(self, head_sha, name, title, summary):
        self.checks.append({"id": next(_ids), "head": head_sha, "name": name, "status": "in_progress", "conclusion": None,
                            "title": title, "summary": summary, "annotations": []})
        return self.checks[-1]

    def check_run_output(self, run_id):
        run = next((r for r in self.checks if r["id"] == run_id), {})
        return {"title": run.get("title"), "summary": run.get("summary")}

    def delete_review_comment(self, comment_id):
        self.deleted.append(comment_id)
        self.review_comments_ = [c for c in self.review_comments_ if c["id"] != comment_id]
        self.threads = [t for t in self.threads if t["comments"][0]["id"] != comment_id]


def context(gh, model, policy_text=EARLY):
    return Context(gh=gh, policy=parse_policy(policy_text), model=model, bot=BOT, log=lambda line: None)


def end_state(gh, number=7):
    """What a reader sees once the review is done: the summary, the comments on
    the lines (with what's become of each), the check runs and the labels."""
    [summary] = [c["body"] for c in gh.issue_comments_[number] if publish.SUMMARY_MARKER in c["body"]]
    threads = sorted((t["path"], t["comments"][0]["body"], t["resolved"], len(t["comments"])) for t in gh.threads)
    lines = sorted((c["path"], c["line"], c["body"]) for c in gh.review_comments_ if not c.get("in_reply_to_id"))
    checks = [{k: v for k, v in run.items() if k != "id"} for run in gh.checks]
    return {"summary": summary, "threads": threads, "lines": lines, "checks": checks, "labels": gh.labels[number]}


def both_ways(answers, *, diff=DIFF, policy_text=EARLY, before=None):
    """The same PR reviewed with the switch off, and with it on (the checks
    first, then the review). `before(gh)` runs on each first, for a PR
    that already had a review. Returns the two GitHubs."""
    off, on = GitHub(diff=diff), GitHub(diff=diff)
    for gh in (off, on):
        if before:
            before(gh)
    review_pull(context(off, FakeModel(*answers), policy_text.replace("early_checks: true", "early_checks: false")), 7)
    early = post_early(context(on, FakeModel(), policy_text), 7)
    assert early.status == "early" and early.early is not None
    review_pull(context(on, FakeModel(*answers), policy_text), 7)
    return off, on


def comments_posted(gh):
    return [c for review in gh.reviews for c in review["comments"]]


# ── off by default ───────────────────────────────────────────────────────


def test_early_checks_are_off_by_default():
    assert parse_policy(None).review.early_checks is False
    assert parse_policy(EARLY).review.early_checks is True


# ── the early post ───────────────────────────────────────────────────────


def test_the_early_post_has_the_checks_and_says_the_model_is_running():
    gh = GitHub(diff=DIFF)
    outcome = post_early(context(gh, FakeModel()), 7)
    [comment] = gh.issue_comments_[7]
    body = comment["body"]
    assert body.startswith(publish.SUMMARY_MARKER)
    assert "**Found by the checks:** 1 high. 1 on the changed lines below." in body
    assert f"**The model's review:** {publish.EARLY_PENDING}" in body
    assert "Suggested by the model" not in body and "**Findings:**" not in body and "**Triage:**" not in body
    # Inline: only the checks' findings; the check run: in progress, with no annotations to repeat later.
    [posted] = comments_posted(gh)
    assert "code/sql" in posted["body"] and posted["line"] == 5
    [run] = gh.checks
    assert run["status"] == "in_progress" and run["conclusion"] is None and run["annotations"] == []
    assert run["title"] == "Checks: 1 high. Model: still running." and run["name"] == "Stratum"
    assert publish.Early.decode(body, HEAD) == outcome.early == publish.Early(HEAD, run["id"], "neutral", outcome.early.inline)
    # Nothing is read as a review: the state is an empty one, as if there were no comment.
    assert publish.State.decode(body) == publish.State()


def test_the_early_post_carries_the_last_reviews_state_byte_for_byte():
    gh = GitHub(diff=DIFF)
    review_pull(context(gh, FakeModel(model_answer())), 7)
    before = gh.issue_comments_[7][0]["body"]
    gh.move_head(7, "b" * 40, FIXED)
    gh.compare[(HEAD, "b" * 40)] = ["app/users.py"]
    post_early(context(gh, FakeModel()), 7)
    [comment] = gh.issue_comments_[7]  # the same comment, edited
    assert publish.state_text(comment["body"]) == publish.state_text(before)
    assert len(gh.resolved) == len(gh.edited) == 0  # no thread is closed before the full review


def test_the_early_post_leaves_out_what_was_ignored_on_the_pr():
    gh = GitHub(diff=DIFF)
    post_early(context(gh, FakeModel()), 7)
    [fp] = publish.Early.decode(gh.issue_comments_[7][0]["body"], HEAD).inline
    # A maintainer ignores the check's finding; on the next push it isn't posted early again.
    comment = gh.issue_comments_[7][0]
    comment["body"] = publish.replace_state(comment["body"], publish.State(ignored=[fp]))
    gh.threads, gh.review_comments_, gh.reviews = [], [], []
    gh.move_head(7, "c" * 40)
    post_early(context(gh, FakeModel()), 7)
    assert gh.reviews == []
    assert "**Found by the checks:** nothing." in gh.issue_comments_[7][0]["body"]


def test_a_commit_already_reviewed_in_full_gets_no_early_post():
    gh = GitHub(diff=DIFF)
    review_pull(context(gh, FakeModel(model_answer())), 7)
    body, runs = gh.issue_comments_[7][0]["body"], len(gh.checks)
    outcome = post_early(context(gh, FakeModel()), 7)  # the job run again on the same commit
    assert outcome.status == "skipped" and outcome.early is None
    assert gh.issue_comments_[7][0]["body"] == body and len(gh.checks) == runs


def test_quiet_and_early_checks_together():
    gh = GitHub(diff=DIFF)
    post_early(context(gh, FakeModel(), "review:\n  early_checks: true\n  quiet: true\n"), 7)
    body = gh.issue_comments_[7][0]["body"]
    assert "**Found by the checks:** 1 high." in body and publish.EARLY_PENDING in body
    assert "**Suggested by the model:** nothing." not in body  # it hasn't said anything yet


# ── the full review completes it ─────────────────────────────────────────


def test_the_end_state_is_the_one_without_early_checks():
    off, on = both_ways([model_answer()])
    assert end_state(on) == end_state(off)
    assert publish.EARLY_PENDING not in end_state(on)["summary"] and "stratum:early" not in end_state(on)["summary"]
    [run] = on.checks  # the run started early was completed, not joined by a second one
    assert run["status"] == "completed" and run["conclusion"] == "neutral"


def test_a_check_finding_posted_early_is_not_posted_again():
    off, on = both_ways([model_answer()])
    fingerprints = [c["body"].split("stratum:finding ")[1][:12] for c in comments_posted(on)]
    assert len(fingerprints) == len(set(fingerprints)) == len(comments_posted(off))
    assert len(on.reviews) == 2 and len(on.reviews[0]["comments"]) == 1  # the check's, then the model's
    assert on.deleted == []


def test_a_model_finding_that_replaces_a_check_finding_takes_its_place():
    # The model reports the SQL line more severely and in the check's category: the review keeps one finding there, the model's.
    sql = 'cur = conn.execute(f"SELECT * FROM users WHERE id = {user_id}")'
    answer = model_answer(findings=[{"file": "app/users.py", "code": sql, "line": 5, "severity": "critical", "category": "security",
                                     "title": "SQL injection", "detail": "user_id goes into the query unescaped.", "fix": "Pass it as a parameter."}])
    off, on = both_ways([answer])
    assert end_state(on) == end_state(off)
    assert len(on.deleted) == 1 and not any("code/sql" in c["body"] for c in on.review_comments_)


def test_the_inline_limit_chooses_as_it_would_without_the_early_post():
    # One place on the lines: without the early post the model's critical finding gets it, so the check's comment goes.
    policy = "review:\n  early_checks: true\n  max_inline_comments: 1\n"
    answer = model_answer(findings=[{"file": "app/users.py", "code": '    print("unreachable")', "line": 7, "severity": "critical",
                                     "category": "bug", "title": "Dead code", "detail": "This line never runs.", "fix": "Remove it."}])
    off, on = both_ways([answer], policy_text=policy)
    assert end_state(on) == end_state(off)
    assert len(on.review_comments_) == 1 and "Dead code" in on.review_comments_[0]["body"] and len(on.deleted) == 1


def test_an_early_comment_someone_replied_to_is_never_deleted():
    policy = "review:\n  early_checks: true\n  max_inline_comments: 1\n"
    answer = model_answer(findings=[{"file": "app/users.py", "code": '    print("unreachable")', "line": 7, "severity": "critical",
                                     "category": "bug", "title": "Dead code", "detail": "This line never runs.", "fix": "Remove it."}])
    gh = GitHub(diff=DIFF)
    post_early(context(gh, FakeModel(), policy), 7)
    [early] = gh.review_comments_
    gh.reply(7, early["id"], "Good catch, fixing.")
    review_pull(context(gh, FakeModel(answer), policy), 7)
    assert gh.deleted == [] and any(c["id"] == early["id"] for c in gh.review_comments_)


def test_the_end_state_matches_on_a_later_push_too():
    def first_review(gh):
        review_pull(context(gh, FakeModel(model_answer()), "review:\n  early_checks: false\n"), 7)
        gh.move_head(7, "b" * 40, FIXED)
        gh.compare[(HEAD, "b" * 40)] = ["app/users.py"]

    off, on = both_ways([model_answer(findings=[])], before=first_review)
    assert end_state(on) == end_state(off)
    assert "Dead code" in {f["title"] for f in publish.State.decode(end_state(on)["summary"]).findings}  # carried over, early post or not


def test_the_end_state_matches_with_quiet_on():
    off, on = both_ways([model_answer()], policy_text="review:\n  early_checks: true\n  quiet: true\n")
    assert end_state(on) == end_state(off)


def test_the_end_state_matches_when_the_model_fails():
    off, on = both_ways([""] * 6)  # empty replies: every call fails, and the review has only the checks
    assert end_state(on) == end_state(off)
    assert "The model didn't answer" in end_state(on)["summary"] or "model calls failed" in end_state(on)["summary"]


# ── when the model never finishes ────────────────────────────────────────


def test_a_review_that_never_comes_leaves_the_early_post_saying_so():
    gh = GitHub(diff=DIFF)
    early = post_early(context(gh, FakeModel()), 7).early
    posted = gh.issue_comments_[7][0]["body"]
    assert stop_early(gh, 7, HEAD, bot=BOT, name="Stratum") is True
    [comment] = gh.issue_comments_[7]
    assert f"**The model's review:** {publish.EARLY_STOPPED}" in comment["body"]
    assert publish.EARLY_PENDING not in comment["body"] and publish.Early.decode(comment["body"], HEAD) is None
    assert publish.state_text(comment["body"]) == publish.state_text(posted)
    assert "**Found by the checks:** 1 high. 1 on the changed lines below." in comment["body"]
    [run] = gh.checks
    assert run["id"] == early.check_run and run["status"] == "completed" and run["conclusion"] == "neutral"
    assert run["title"] == "Checks: 1 high. Model: didn't finish."
    # A later review of the same head (`/stratum review`, a re-run) edits the same comment, as usual.
    review_pull(context(gh, FakeModel(model_answer())), 7)
    [comment] = gh.issue_comments_[7]
    assert publish.EARLY_STOPPED not in comment["body"] and "**What this PR does:**" in comment["body"]


def test_a_critical_check_finding_fails_the_check_when_the_model_never_finishes():
    secret = 'diff --git a/app/config.py b/app/config.py\n--- /dev/null\n+++ b/app/config.py\n@@ -0,0 +1 @@\n+API_TOKEN = "ghp_' + "a1B2c3D4e5F6g7H8i9J0" * 2 + '"\n'
    gh = GitHub(diff=secret)
    assert post_early(context(gh, FakeModel()), 7).early.conclusion == "failure"
    stop_early(gh, 7, HEAD, bot=BOT, name="Stratum")
    assert gh.checks[-1]["conclusion"] == "failure"


def test_stopping_leaves_a_newer_commits_early_post_alone():
    gh = GitHub(diff=DIFF)
    old = post_early(context(gh, FakeModel()), 7).early
    gh.move_head(7, "b" * 40)
    post_early(context(gh, FakeModel()), 7)  # the next push's job, which cancelled this one
    newer = gh.issue_comments_[7][0]["body"]
    assert stop_early(gh, 7, HEAD, bot=BOT, name="Stratum", run_id=old.check_run, conclusion="neutral") is False
    assert gh.issue_comments_[7][0]["body"] == newer
    old_run = next(r for r in gh.checks if r["id"] == old.check_run)
    assert old_run["status"] == "completed" and old_run["title"] == "Checks: 1 high. Model: didn't finish."
    assert [r["status"] for r in gh.checks if r["head"] == "b" * 40] == ["in_progress"]


# ── the Action's steps ───────────────────────────────────────────────────


def event_env(tmp_path, monkeypatch, gh, payload, **extra):
    event = tmp_path / "event.json"
    event.write_text(json.dumps(payload))
    monkeypatch.setattr(action, "GitHub", lambda token, repo, api: gh)
    output = tmp_path / "output"
    output.write_text("")
    return {"GITHUB_EVENT_NAME": "pull_request_target", "GITHUB_EVENT_PATH": str(event), "GITHUB_REPOSITORY": "acme/widget",
            "STRATUM_GITHUB_TOKEN": "t", "STRATUM_BOT_LOGIN": BOT, "GITHUB_OUTPUT": str(output), **extra}


def pr_payload(head=HEAD):
    return {"action": "opened", "repository": {"default_branch": "main"}, "pull_request": {"number": 7, "draft": False, "head": {"sha": head}}}


def outputs(env):
    with open(env["GITHUB_OUTPUT"], encoding="utf-8") as handle:
        return dict(line.split("=", 1) for line in handle.read().splitlines() if line)


def test_the_early_step_does_nothing_with_the_switch_off(tmp_path, monkeypatch):
    gh = GitHub(diff=DIFF)
    monkeypatch.setattr(action, "model_from_policy", lambda policy, env: FakeModel())
    env = event_env(tmp_path, monkeypatch, gh, pr_payload())
    assert action.early(env) == 0
    assert gh.issue_comments_[7] == [] and gh.reviews == [] and gh.checks == [] and outputs(env) == {}


@pytest.mark.parametrize("model", [None, "a model"])
def test_the_early_step_posts_only_when_a_model_will_review(tmp_path, monkeypatch, model):
    gh = GitHub(diff=DIFF, files={(".stratum/policy.yml", "main"): EARLY})
    monkeypatch.setattr(action, "model_from_policy", lambda policy, env: FakeModel() if model else None)
    env = event_env(tmp_path, monkeypatch, gh, pr_payload())
    assert action.early(env) == 0
    if model is None:  # checks only: the review step is as quick as this one
        assert gh.issue_comments_[7] == [] and outputs(env) == {}
    else:
        [run] = gh.checks
        assert outputs(env) == {"posted": "true", "check-run": str(run["id"]), "conclusion": "neutral"}


def test_the_early_step_never_fails_the_job(tmp_path, monkeypatch, capsys):
    gh = GitHub(diff=DIFF, files={(".stratum/policy.yml", "main"): EARLY})
    monkeypatch.setattr(action, "model_from_policy", lambda policy, env: FakeModel())
    monkeypatch.setattr(action, "post_early", lambda ctx, number: 1 / 0)
    assert action.early(event_env(tmp_path, monkeypatch, gh, pr_payload())) == 0
    assert "::warning::Stratum couldn't post the checks first (ZeroDivisionError" in capsys.readouterr().out


def test_the_steps_together_and_the_stopped_step(tmp_path, monkeypatch):
    gh = GitHub(diff=DIFF, files={(".stratum/policy.yml", "main"): EARLY})
    monkeypatch.setattr(action, "model_from_policy", lambda policy, env: FakeModel(model_answer()))
    env = event_env(tmp_path, monkeypatch, gh, pr_payload())
    action.early(env)
    early = {"STRATUM_EARLY_CHECK_RUN": outputs(env)["check-run"], "STRATUM_EARLY_CONCLUSION": outputs(env)["conclusion"]}
    assert action.stopped({**env, **early}) == 0  # Ollama didn't start: the review step never ran
    assert publish.EARLY_STOPPED in gh.issue_comments_[7][0]["body"] and gh.checks[0]["status"] == "completed"


def test_a_failing_review_step_completes_the_early_run_instead_of_adding_one(tmp_path, monkeypatch):
    gh = GitHub(diff=DIFF, files={(".stratum/policy.yml", "main"): EARLY})
    monkeypatch.setattr(action, "model_from_policy", lambda policy, env: FakeModel())
    env = event_env(tmp_path, monkeypatch, gh, pr_payload())
    action.early(env)
    run_id = outputs(env)["check-run"]
    monkeypatch.setattr(action, "review_pull", lambda ctx, number: 1 / 0)
    assert action.main({**env, "STRATUM_EARLY_CHECK_RUN": run_id}) == 1
    [run] = gh.checks
    assert run["status"] == "completed" and run["title"] == "Stratum couldn't finish"


def test_a_review_that_finds_the_pr_closed_says_the_model_didnt_finish(tmp_path, monkeypatch):
    gh = GitHub(diff=DIFF, files={(".stratum/policy.yml", "main"): EARLY})
    monkeypatch.setattr(action, "model_from_policy", lambda policy, env: FakeModel(model_answer()))
    env = event_env(tmp_path, monkeypatch, gh, pr_payload())
    action.early(env)
    gh.pulls[7]["state"] = "closed"
    assert action.main({**env, "STRATUM_EARLY_CHECK_RUN": outputs(env)["check-run"]}) == 0
    assert publish.EARLY_STOPPED in gh.issue_comments_[7][0]["body"]
    assert [r["status"] for r in gh.checks] == ["completed"]


def test_the_api_completes_the_run_it_started_and_posts_one_when_its_gone(monkeypatch):
    from stratum_open.github.api import GitHub as RealGitHub, GitHubError

    gh = RealGitHub("t", "acme/widget")
    sent = []

    def request(method, url, **kwargs):
        sent.append((method, url.rsplit("/acme/widget", 1)[1], kwargs.get("json")))
        if method == "PATCH" and url.endswith("/check-runs/404"):
            raise GitHubError(404, "Not Found")
        return {"id": 9}

    monkeypatch.setattr(gh, "request", request)
    gh.start_check_run(HEAD, "Stratum", "Checks: nothing. Model: still running.", "text")
    gh.check_run(HEAD, "Stratum", "success", "No findings", "text", "", [], run_id=9)
    gh.check_run(HEAD, "Stratum", "success", "No findings", "text", "", [], run_id=404)
    gh.delete_review_comment(12)
    assert [(method, path) for method, path, _ in sent] == [
        ("POST", "/check-runs"), ("PATCH", "/check-runs/9"), ("PATCH", "/check-runs/404"), ("POST", "/check-runs"), ("DELETE", "/pulls/comments/12")]
    assert sent[0][2]["status"] == "in_progress" and "conclusion" not in sent[0][2]
    assert sent[1][2]["status"] == "completed" and sent[1][2]["conclusion"] == "success" and "name" not in sent[1][2]


def test_the_end_state_matches_with_learning_from_history_on():
    # A check rule maintainers voted down on five earlier PRs: the early post
    # leaves it out too, so it never goes up only to be taken down, and the
    # comments it posts ask for votes as the full review's do.
    from test_learn import first_review, react, stratum_comment  # noqa: F401  (stratum_comment: same ids)

    check = next(c for c in first_review().values() if "a check (`" in c["body"])
    rule = publish._FINDING.search(check["body"]).group(2)
    voters = ("mona", "lisa", "otto", "rob", "sam")

    def before(gh):
        gh.permissions = dict.fromkeys(voters, "write")
        for pr, login in zip((10, 11, 12, 13, 14), voters):
            old = dict(check, id=next(_ids), pull_request_url=f"https://api.github.com/repos/acme/widget/pulls/{pr}", reactions={"-1": 1})
            react(gh, old, (login, "-1"))
            gh.history_review_comments.append(old)

    policy_text = EARLY + "  learn_from_history: true\n"
    off, on = both_ways([model_answer()], policy_text=policy_text, before=before)
    assert end_state(on) == end_state(off)
    assert on.deleted == []  # nothing posted early had to be taken down
    assert not any(rule in c["body"] for c in comments_posted(on))
    assert all("👍 or 👎 tells Stratum" in c["body"] for c in comments_posted(on))
