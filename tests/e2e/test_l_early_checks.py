"""`review.early_checks` as the Action runs it: `stratum-open action --early`
before the model starts, then `stratum-open action`, as real processes
against the fakes. The end state is the one the review step alone posts."""

from __future__ import annotations

import pytest

from conftest import DIFF, SQL_LINE, head_of
from contract import assert_check_completed, assert_contract, assert_no_traceback, run_stratum
from fake_github import FakeGitHubServer
from fake_model import Response, finding, json_reply
from test_cli_process import action_env, pr_payload

from stratum_open.github import publish

HEAD = head_of(7)
EARLY = "review:\n  early_checks: true\n"


def end_state(fake: FakeGitHubServer) -> dict:
    [summary] = fake.summary_comments(7)
    lines = sorted((c["path"], c["line"], c["body"]) for c in fake.inline_comments(7))
    runs = [{k: run.get(k) for k in ("name", "head_sha", "status", "conclusion", "output")} for run in fake.check_runs_for(HEAD)]
    return {"summary": summary["body"], "lines": lines, "runs": runs, "labels": fake.labels_on(7)}


def outputs(path) -> dict[str, str]:
    return dict(line.split("=", 1) for line in path.read_text(encoding="utf-8").splitlines() if line)


def early_env(fake, fake_model, tmp_path):
    out = tmp_path / "github_output"
    out.write_text("", encoding="utf-8")
    return {**action_env(fake, fake_model, tmp_path, pr_payload()), "GITHUB_OUTPUT": str(out)}, out


@pytest.mark.parametrize("limit, dead_code", [
    ("", "medium"),
    # One place on the lines, which the model's critical finding takes: the check's early comment is deleted.
    ("  max_inline_comments: 1\n", "critical"),
])
def test_the_checks_first_then_the_review_ends_as_the_review_alone(fake_model, tmp_path, limit, dead_code):
    fake_model.default = Response.ok(json_reply([finding("app/users.py", SQL_LINE, line=5), finding("app/users.py", 'print("unreachable")', line=7,
                                                                                                    title="Dead code", severity=dead_code)]))
    with FakeGitHubServer() as off, FakeGitHubServer() as on:
        for fake, policy in ((off, "review:\n  early_checks: false\n" + limit), (on, EARLY + limit)):
            fake.add_pull(7, diff=DIFF)
            fake.set_policy(policy)
        alone = run_stratum(["action"], env=action_env(off, fake_model, tmp_path, pr_payload()))
        assert alone.returncode == 0 and "PR #7: published" in alone.stdout, alone.stderr

        env, out = early_env(on, fake_model, tmp_path)
        calls = len(fake_model.calls)
        first = run_stratum(["action", "--early"], env=env)
        assert first.returncode == 0 and "the model's review follows" in first.stdout, first.stderr
        assert_no_traceback(first.stderr, first.stdout)
        assert len(fake_model.calls) == calls  # the model wasn't needed
        [run] = on.check_runs_for(HEAD)
        assert run["status"] == "in_progress"
        assert publish.EARLY_PENDING in on.summary_comments(7)[0]["body"] and len(on.inline_comments(7)) == 1
        given = outputs(out)
        assert given == {"posted": "true", "check-run": str(run["id"]), "conclusion": "neutral"}

        review = run_stratum(["action"], env={**env, "STRATUM_EARLY_CHECK_RUN": given["check-run"], "STRATUM_EARLY_CONCLUSION": given["conclusion"]})
        assert review.returncode == 0 and "PR #7: published" in review.stdout, review.stderr
        assert end_state(on) == end_state(off)
        assert len(on.requests_to("DELETE", r"/pulls/comments/")) == (1 if limit else 0)
        assert_contract(on, number=7, head=HEAD, stderr=review.stderr, stdout=review.stdout)


def test_with_the_switch_off_the_early_step_posts_nothing(fake_github, fake_model, tmp_path):
    fake_github.add_pull(7, diff=DIFF)
    env, out = early_env(fake_github, fake_model, tmp_path)
    result = run_stratum(["action", "--early"], env=env)
    assert result.returncode == 0 and result.stdout == "" and outputs(out) == {}
    assert fake_github.issue_comments[7] == [] and fake_github.check_runs == [] and fake_github.reviews == []


def test_when_the_review_never_runs_the_stopped_step_says_so(fake_github, fake_model, tmp_path):
    fake_github.add_pull(7, diff=DIFF)
    fake_github.set_policy(EARLY)
    env, out = early_env(fake_github, fake_model, tmp_path)
    assert run_stratum(["action", "--early"], env=env).returncode == 0
    given = outputs(out)
    # Ollama didn't start, so the review step was skipped; the last step runs.
    result = run_stratum(["action", "--stopped"], env={**env, "STRATUM_EARLY_CHECK_RUN": given["check-run"], "STRATUM_EARLY_CONCLUSION": given["conclusion"]})
    assert result.returncode == 0, result.stderr
    assert_no_traceback(result.stderr, result.stdout)
    [summary] = fake_github.summary_comments(7)
    assert publish.EARLY_STOPPED in summary["body"] and publish.EARLY_PENDING not in summary["body"]
    [run] = assert_check_completed(fake_github, HEAD)
    assert run["conclusion"] == "neutral" and run["output"]["title"] == "Checks: 1 high. Model: didn't finish."
