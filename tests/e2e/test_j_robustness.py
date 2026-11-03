"""J. Resources and robustness: bounded memory and time, interruptions, and
no secrets in what's written down."""

from __future__ import annotations

import time
import tracemalloc

import pytest

from stratum_open import cli
from stratum_open.github.api import GitHub
from stratum_open.model import Model
from stratum_open.runner import review_pull
from stratum_open.storage import redact

import diffs
from conftest import DIFF, head_of
from contract import assert_contract, assert_no_secrets
from fake_model import Response

HEAD = head_of(7)


@pytest.mark.slow
def test_memory_stays_bounded_on_a_huge_diff(fake_github, make_context, logs):
    diff = diffs.many_files(1500, lines=40) + diffs.one_huge_file(60_000) + diffs.long_line(300_000)
    fake_github.add_pull(7, diff=diff)
    tracemalloc.start()
    outcome = review_pull(make_context(), 7)
    _, peak = tracemalloc.get_traced_memory()
    tracemalloc.stop()
    assert outcome.status == "published"
    assert peak < 40 * len(diff), f"peak {peak / 1e6:.0f} MB for a {len(diff) / 1e6:.1f} MB diff"
    assert_contract(fake_github, number=7, head=HEAD, logs=logs)


def test_the_run_gives_up_on_the_model_within_its_timeout_and_completes(fake_github, make_context, make_model, fake_model, logs):
    fake_github.add_pull(7, diff=diffs.many_files(40, lines=200))
    fake_model.default = Response.hang(seconds=30)
    started = time.monotonic()
    outcome = review_pull(make_context(policy_text="review:\n  max_chunks: 2\n  context_tokens: 4096\n", model=make_model(timeout=1, retries=0)), 7)
    elapsed = time.monotonic() - started
    assert outcome.status == "published" and elapsed < 15, f"{elapsed:.1f}s"
    assert len(fake_model.chat_calls) <= 2
    assert_contract(fake_github, number=7, head=HEAD, logs=logs)


def test_ctrl_c_mid_run_leaves_no_check_in_progress(fake_github, fake_model, monkeypatch, capsys):
    fake_github.add_pull(7, diff=DIFF)
    monkeypatch.setenv("GITHUB_TOKEN", fake_github.token)
    monkeypatch.setenv("STRATUM_GITHUB_API", fake_github.url)
    monkeypatch.setenv("STRATUM_MODEL_ENDPOINT", fake_model.url)
    monkeypatch.setenv("STRATUM_BOT_LOGIN", fake_github.bot)
    monkeypatch.setattr(Model, "complete", lambda self, *a, **k: (_ for _ in ()).throw(KeyboardInterrupt()))
    assert cli.main(["review", f"{fake_github.repo}#7", "--post"]) == 130
    assert "Stopped." in capsys.readouterr().err
    assert all(run.get("status") == "completed" for run in fake_github.check_runs)  # by construction, Stratum never creates another kind


def test_logs_and_posted_text_never_carry_tokens(fake_github, make_context, logs):
    fake_github.add_pull(7, diff=DIFF)
    fake_github.fail("POST", r"/pulls/7/reviews", 500, body={"message": f"server error while handling Bearer {fake_github.token} for ghp_AAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAA"}, times=0)
    ctx = make_context()
    ctx.log = lambda line: logs.append(redact(line))
    review_pull(ctx, 7)
    assert any("couldn't post inline comments" in line for line in logs)
    assert_no_secrets(fake_github, *logs, secrets=["ghp_AAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAA"])


def test_a_failed_git_command_is_described_without_its_arguments():
    """The App runs git with the installation token in an authorization header
    argument; a failed command's description must never echo the command."""
    import subprocess

    from stratum_open.track_record import describe_subprocess_error

    secret = "basic " + "eC1hY2Nlc3MtdG9rZW46Z2hzX3NlY3JldFRva2Vu"
    cmd = ["git", "-c", f"http.extraHeader=AUTHORIZATION: {secret}", "fetch", "origin"]
    quiet = subprocess.CalledProcessError(128, cmd, stderr="")
    assert describe_subprocess_error(quiet) == "exit status 128"
    noisy = subprocess.CalledProcessError(128, cmd, stderr="fatal: could not read from remote repository\n")
    assert describe_subprocess_error(noisy) == "fatal: could not read from remote repository"
    slow = subprocess.TimeoutExpired(cmd, 600)
    assert describe_subprocess_error(slow) == "timed out after 600 s"
    for text in (describe_subprocess_error(quiet), describe_subprocess_error(slow), redact(f"header {secret}")):
        assert "eC1hY2Nl" not in text and "extraHeader" not in text


def test_redact_covers_every_credential_shape():
    text = ("token ghp_" + "A" * 36 + " key sk-" + "B" * 40 + " jwt eyJhbGciOiJSUzI1NiJ9.eyJzdWIiOiIxMjM0In0.SflKxwRJSMeKKF2QT4fwpMeJf36POk6yJV "
            "Bearer abcdefghijklmnopqrstuvwxyz0123456789 password=hunter2hunter2 -----BEGIN RSA PRIVATE KEY-----\nMIIE\n-----END RSA PRIVATE KEY-----")
    cleaned = redact(text)
    for shape in ("ghp_AAAA", "sk-BBBB", "eyJhbGci", "abcdefghijklmnop", "hunter2", "MIIE"):
        assert shape not in cleaned
    assert cleaned.count("[redacted]") >= 6


def test_github_client_has_timeouts_everywhere(fake_github):
    """Every call `GitHub` makes passes a timeout: a hang would otherwise look like a crash in CI."""
    import inspect

    from stratum_open.github import api

    source = inspect.getsource(api)
    calls = [line for line in source.splitlines() if "self.session." in line and ("get(" in line or "request(" in line or "post(" in line)]
    assert calls and all("timeout=" in line for line in calls), calls
