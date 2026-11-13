"""review.learn_from_history: the Action leaves out findings like the ones
maintainers rejected on recent PRs, read from GitHub at review time."""

import itertools

import pytest
import requests
from fakes import FakeGitHub, FakeModel
from test_runner import BOT, DIFF, context, model_answer

from stratum_open.github import feedback, publish
from stratum_open.github.api import GitHub, GitHubError
from stratum_open.policy import parse_policy
from stratum_open.runner import review_pull

LEARN = parse_policy("review:\n  learn_from_history: true\n")
_ids = itertools.count(1)
URL = "https://api.github.com/repos/acme/widget"


def stratum_comment(fp="0123456789ab", rule="workflow/unpinned-action", pr=3, down=0, up=0, author=BOT):
    return {"id": next(_ids), "body": f"**Medium · workflow:** something\n<!-- stratum:finding {fp} {rule} -->",
            "user": {"login": author}, "pull_request_url": f"{URL}/pulls/{pr}", "reactions": {"+1": up, "-1": down}}


def react(gh, comment, *votes):
    gh.reactions_[comment["id"]] = [{"content": content, "user": {"login": login}} for login, content in votes]


def reply(root, body, login, pr=3):
    return {"id": next(_ids), "body": body, "in_reply_to_id": root["id"], "user": {"login": login}, "pull_request_url": f"{URL}/pulls/{pr}"}


@pytest.fixture
def gh():
    return FakeGitHub(diff=DIFF, permissions={"mona": "write", "lisa": "maintain", "otto": "admin", "rob": "write", "sam": "write"})


# ── what counts ──────────────────────────────────────────────────────────


def test_a_rule_is_muted_by_the_apps_thresholds(gh):
    a, b, c = (stratum_comment(pr=pr, down=1) for pr in (3, 4, 5))
    gh.history_review_comments = [a, b, c, reply(a, "/stratum ignore", "lisa")]
    for comment in (a, b, c):
        react(gh, comment, ("mona", "-1"))
    # mona's 👎 on three comments and lisa's ignore reply: four votes, one short of five.
    assert feedback.learn(gh, BOT).rules == {}

    gh.issue_comments_[9] = [{"id": next(_ids), "body": "/stratum ignore workflow/unpinned-action", "user": {"login": "rob"}}]
    assert feedback.learn(gh, BOT).rules == {"workflow/unpinned-action": [3, 4, 5, 9]}

    # 3 of 5 votes 👎 is the App's 60%: still muted. A third 👍 makes it 3 of 6, and it isn't.
    d, e = stratum_comment(pr=6, up=1), stratum_comment(pr=6, up=1)
    react(gh, d, ("sam", "+1"))
    react(gh, e, ("otto", "+1"))
    gh.history_review_comments = [a, b, d, e]
    gh.issue_comments_[9] = []
    gh.history_review_comments.append(reply(a, "/stratum ignore", "lisa"))
    assert feedback.learn(gh, BOT).rules == {"workflow/unpinned-action": [3, 4]}
    f = stratum_comment(pr=6, up=1)
    react(gh, f, ("rob", "+1"))
    gh.history_review_comments.append(f)
    assert feedback.learn(gh, BOT).rules == {}


def test_only_people_with_write_access_count(gh):
    comments = [stratum_comment(pr=pr, down=1) for pr in range(3, 9)]
    for comment in comments:
        react(gh, comment, ("erin", "-1"), ("dependabot[bot]", "-1"))  # erin can only read; a bot never counts
    gh.history_review_comments = comments + [reply(comments[0], "/stratum ignore", "erin")]
    gh.issue_comments_[9] = [{"id": next(_ids), "body": "/stratum ignore workflow/unpinned-action", "user": {"login": "erin"}}]
    assert feedback.learn(gh, BOT).rules == {}


def test_each_person_counts_once_per_comment_and_once_per_pr_for_a_rule(gh):
    a = stratum_comment(down=1)
    react(gh, a, ("mona", "-1"))
    gh.history_review_comments = [a] + [reply(a, "/stratum ignore", "mona") for _ in range(4)]
    gh.issue_comments_[3] = [{"id": next(_ids), "body": "/stratum ignore workflow/unpinned-action", "user": {"login": "mona"}}
                             for _ in range(4)]
    # mona's 👎 and her four ignore replies are one vote; her four `ignore <rule>` on #3 one more: two in all.
    assert feedback.learn(gh, BOT).rules == {}
    assert feedback.learn(gh, BOT).fingerprints == {}  # a check's finding is never hidden by its fingerprint


def test_a_comment_not_written_by_stratum_is_never_counted(gh):
    forged = [stratum_comment(pr=pr, down=1, author="mallory") for pr in range(3, 9)]
    for comment in forged:
        react(gh, comment, ("mona", "-1"))
    gh.history_review_comments = forged
    assert feedback.learn(gh, BOT).rules == {}


def test_a_model_finding_is_hidden_by_its_fingerprint_once_voted_down_more_than_up(gh):
    a = stratum_comment(fp="aaaaaaaaaaaa", rule="model:dead-code", down=1, up=1)
    react(gh, a, ("mona", "-1"), ("lisa", "+1"))
    gh.history_review_comments = [a]
    assert feedback.learn(gh, BOT).fingerprints == {}
    react(gh, a, ("mona", "-1"), ("lisa", "+1"), ("otto", "-1"))
    assert feedback.learn(gh, BOT).fingerprints == {"aaaaaaaaaaaa": [3]}


# ── cost and failures ────────────────────────────────────────────────────


def test_reads_are_bounded(gh):
    asked = {"reactions": 0, "permission": 0}

    class Counting(FakeGitHub):
        def reactions(self, comment_id):
            asked["reactions"] += 1
            return super().reactions(comment_id)

        def permission(self, login):
            asked["permission"] += 1
            return "write"

    counting = Counting(diff=DIFF)
    comments = [stratum_comment(fp=f"{n:012x}", rule="model:x", pr=n, down=1) for n in range(60)]
    for n, comment in enumerate(comments):
        react(counting, comment, (f"person{n}", "-1"))
    counting.history_review_comments = comments
    learned = feedback.learn(counting, BOT)
    assert asked == {"reactions": feedback.MAX_REACTION_READS, "permission": feedback.MAX_PEOPLE}
    assert len(learned.fingerprints) == feedback.MAX_PEOPLE  # the people past the limit aren't counted


def test_the_thumbs_down_are_read_before_the_thumbs_up(gh):
    ups = [stratum_comment(fp=f"{n:012x}", rule=f"model:up-{n}", up=1) for n in range(30)]
    down = stratum_comment(fp="dddddddddddd", rule="model:down", pr=4, down=1)
    react(gh, down, ("mona", "-1"))
    gh.history_review_comments = ups + [down]  # the 👍'd ones are newer, and about other findings: none is read
    assert feedback.learn(gh, BOT).fingerprints == {"dddddddddddd": [4]}


def test_github_refusing_a_read_hides_nothing_and_says_so(gh):
    class Refusing(FakeGitHub):
        def recent_review_comments(self, limit):
            raise GitHubError(403, "API rate limit exceeded")

    learned = feedback.learn(Refusing(diff=DIFF), BOT)
    assert learned.rules == learned.fingerprints == {}
    assert learned.problem == "Couldn't read the repo's earlier inline comments (GitHub answered 403), so none were learned from."

    class Offline(FakeGitHub):
        def recent_issue_comments(self, limit):
            raise requests.ConnectionError("down")

        def reactions(self, comment_id):
            raise GitHubError(403, "Resource not accessible by integration")

    offline = Offline(diff=DIFF)
    offline.history_review_comments = [stratum_comment(down=1)]
    assert "`/stratum ignore <rule>` there wasn't counted" in feedback.learn(offline, BOT).problem


def test_recent_comments_cost_one_call_per_hundred(monkeypatch):
    gh = GitHub("t", "acme/widget")
    calls = []

    class Page:
        status_code, content = 200, b"[...]"

        def __init__(self, n):
            self.headers = {"Link": f'<https://api.github.com/next?page={n + 1}>; rel="next"'}  # always more
            self.items = [{"id": n * 100 + i} for i in range(100)]

        def json(self):
            return self.items

    def send(method, url, **kwargs):
        calls.append((url, kwargs.get("params")))
        return Page(len(calls))

    monkeypatch.setattr(gh, "_send", send)
    assert len(gh.recent_review_comments(150)) == 150 and len(calls) == 2
    assert calls[0] == ("/repos/acme/widget/pulls/comments", {"per_page": 100, "sort": "created", "direction": "desc"})
    calls.clear()
    assert len(gh.recent_issue_comments(30)) == 30 and len(calls) == 1
    assert calls[0] == ("/repos/acme/widget/issues/comments", {"per_page": 30, "sort": "created", "direction": "desc"})


# ── in a review ──────────────────────────────────────────────────────────


def test_off_by_default_it_reads_nothing(gh):
    class Untouchable(FakeGitHub):
        def recent_review_comments(self, limit):
            raise AssertionError("read with learn_from_history off")

        recent_issue_comments = recent_review_comments

    untouchable = Untouchable(diff=DIFF)
    review_pull(context(untouchable, FakeModel(model_answer())), 7)
    body = untouchable.issue_comments_[7][0]["body"]
    assert "Hidden" not in body and "Learned from" not in body
    assert parse_policy(None).review.learn_from_history is False


def first_review():
    """The comments a first review of DIFF posts, as another PR's history."""
    earlier = FakeGitHub(diff=DIFF)
    review_pull(context(earlier, FakeModel(model_answer())), 7)
    return {c["body"].split("\n", 1)[0]: c for c in earlier.review_comments_}


def test_a_finding_rejected_on_an_earlier_pr_is_left_out_and_the_summary_says_so(gh):
    dead = next(c for title, c in first_review().items() if "Dead code" in title)
    old = dict(dead, id=next(_ids), pull_request_url=f"{URL}/pulls/123", reactions={"-1": 1})
    react(gh, old, ("mona", "-1"))
    gh.history_review_comments = [old]

    outcome = review_pull(context(gh, FakeModel(model_answer()), policy=LEARN), 7)
    assert "Dead code" not in {f.title for f in outcome.findings} and "Unknown id" in {f.title for f in outcome.findings}
    assert not any("Dead code" in c["body"] for review in gh.reviews for c in review["comments"])
    body = gh.issue_comments_[7][0]["body"]
    assert "**Hidden:** 1 comment like ones maintainers rejected on #123." in body
    assert "Learned from the repo's last 1 inline comment" in body
    assert "Left out for that: Dead code (`app/users.py` line 7)." in body
    assert "👍 or 👎 tells Stratum" in gh.reviews[0]["comments"][0]["body"]  # votes now count, so the comments ask for them


def test_a_check_rule_muted_by_votes_is_left_out(gh):
    check = next(c for c in first_review().values() if "a check (`" in c["body"])
    rule = publish._FINDING.search(check["body"]).group(2)
    for pr, login in zip((10, 11, 12, 13, 14), ("mona", "lisa", "otto", "rob", "sam")):
        old = dict(check, id=next(_ids), pull_request_url=f"{URL}/pulls/{pr}", reactions={"-1": 1})
        react(gh, old, (login, "-1"))
        gh.history_review_comments.append(old)

    outcome = review_pull(context(gh, FakeModel(model_answer()), policy=LEARN), 7)
    assert rule not in {f.rule for f in outcome.findings}
    body = gh.issue_comments_[7][0]["body"]
    assert "**Hidden:** 1 comment like ones maintainers rejected on #10, #11, #12, #13 and #14." in body
    assert f"Muted by maintainers' 👎: `{rule}`" in body


def test_a_thumbs_down_on_this_pr_closes_the_thread_on_the_next_push(gh):
    ctx = context(gh, FakeModel(model_answer(), model_answer()), policy=LEARN)
    review_pull(ctx, 7)
    dead = next(c for c in gh.review_comments_ if "Dead code" in c["body"])
    dead["reactions"] = {"-1": 1}
    react(gh, dead, ("mona", "-1"))
    gh.move_head(7, "b" * 40)
    gh.compare[("a" * 40, "b" * 40)] = ["app/users.py"]
    review_pull(ctx, 7)
    thread = next(t for t in gh.threads if "Dead code" in t["comments"][0]["body"])
    assert "Hidden: maintainers rejected comments like this one on this PR." in thread["comments"][0]["body"]
    assert "**Hidden:** 1 comment like ones maintainers rejected on this PR." in gh.issue_comments_[7][0]["body"]


def test_a_review_goes_on_when_the_history_cant_be_read():
    class Limited(FakeGitHub):
        def recent_review_comments(self, limit):
            raise GitHubError(403, "API rate limit exceeded")

    limited = Limited(diff=DIFF)
    outcome = review_pull(context(limited, FakeModel(model_answer()), policy=LEARN), 7)
    assert outcome.status == "published" and "Dead code" in {f.title for f in outcome.findings}
    body = limited.issue_comments_[7][0]["body"]
    assert "Couldn't read the repo's earlier inline comments (GitHub answered 403)" in body and "**Hidden:**" not in body


def test_the_lookback_is_checked():
    assert parse_policy("review:\n  learn_lookback: 0\n").errors == ("review.learn_lookback must be at least 1.",)
    policy = parse_policy("review:\n  learn_lookback: 5000\n")
    assert policy.review.learn_lookback == 500 and policy.warnings == ("review.learn_lookback is read as 500, the most it can be.",)


def test_an_old_at_stratum_ignore_is_not_a_rejection(gh):
    """Since commands became `/stratum`, `@stratum ignore` does nothing on GitHub, so it isn't counted either."""
    comments = [stratum_comment(pr=pr) for pr in range(3, 9)]
    gh.history_review_comments = comments + [reply(c, "@stratum ignore", "mona") for c in comments]
    gh.issue_comments_[9] = [{"id": next(_ids), "body": "@stratum ignore workflow/unpinned-action", "user": {"login": login}}
                             for login in ("mona", "lisa", "otto", "rob", "sam")]
    assert feedback.learn(gh, BOT).rules == {}
    gh.history_review_comments += [reply(c, "/stratum ignore", "mona") for c in comments[:5]]
    assert feedback.learn(gh, BOT).rules == {"workflow/unpinned-action": [3, 4, 5, 6, 7]}


def test_the_actions_mute_reply_points_to_learning_when_it_is_on(gh):
    from test_runner import comment_event

    from stratum_open.github.commands import MUTE_LEARNS, MUTE_NEEDS_APP, handle

    for policy, expected in ((parse_policy(None), MUTE_NEEDS_APP), (LEARN, MUTE_LEARNS)):
        event, payload = comment_event("/stratum mute deps/new", login="mona")
        assert handle(context(gh, None, policy=policy), event, payload) == "mute"
        assert gh.issue_comments_[7][-1]["body"] == expected
    assert "needs somewhere to store it" in MUTE_NEEDS_APP and "review.learn_from_history" in MUTE_LEARNS
