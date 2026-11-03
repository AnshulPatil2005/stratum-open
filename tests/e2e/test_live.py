"""Live: the real model (Ollama on localhost, or STRATUM_MODEL_ENDPOINT)
against the recorded PRs and a local repo. `pytest -m live`. Skipped, not
failed, when no model answers. STRATUM_E2E_RECORD=1 saves each raw reply to
fixtures/model_outputs/, which the fake-mode replay test then feeds through
the parser."""

from __future__ import annotations

import json
import os
import subprocess
import time
from pathlib import Path

import pytest

from stratum_open.model import Model
from stratum_open.runner import review_pull

from conftest import DIFF, head_of, live_endpoint, live_model_name
from contract import assert_contract, assert_idempotent, assert_no_traceback, run_stratum

pytestmark = [pytest.mark.live, pytest.mark.slow]
RECORD = os.environ.get("STRATUM_E2E_RECORD") == "1"
OUT = Path(__file__).parent / "fixtures" / "model_outputs"


class Recording(Model):
    def __init__(self, *args, tag: str, **kwargs):
        super().__init__(*args, **kwargs)
        self.tag, self.calls = tag, 0

    def complete(self, system, user, **kwargs):
        reply = super().complete(system, user, **kwargs)
        self.calls += 1
        if RECORD:
            OUT.mkdir(parents=True, exist_ok=True)
            (OUT / f"{self.tag}_{self.calls}.json").write_text(json.dumps({
                "ok": reply.ok, "error": reply.error, "seconds": round(reply.seconds, 1), "prompt_tokens": reply.prompt_tokens,
                "output_tokens": reply.output_tokens, "text": reply.text}, indent=1), encoding="utf-8")
        return reply


def live_model(tag: str, *, name: str | None = None, timeout: float = 600) -> Model:
    return Recording(live_endpoint(), name or live_model_name(), context_tokens=16384, timeout=timeout, retries=1, tag=tag)


@pytest.mark.parametrize("number", [1, 2])
def test_recorded_upsol_prs_with_the_real_model(fake_github, make_context, logs, number):
    pull = fake_github.load_fixture("upSol", number)
    from stratum_open.github.api import GitHub
    ctx = make_context(model=live_model(f"upsol_pr{number}"))
    ctx.gh = GitHub(fake_github.token, fake_github.repo, api=fake_github.url)
    ctx.bot = fake_github.bot
    started = time.monotonic()
    outcome = review_pull(ctx, number)
    assert time.monotonic() - started < 900
    assert outcome.status == "published", outcome
    assert outcome.review is not None and outcome.review.ran, outcome.review.calls
    head = pull["head"]["sha"]
    assert_contract(fake_github, number=number, head=head, logs=logs)
    body = fake_github.summary_comments(number)[0]["body"]
    assert "**What this PR does:**" in body and "didn't answer" not in body
    assert_idempotent(fake_github, number, lambda: review_pull(ctx, number))


def test_a_model_that_is_not_pulled_degrades_with_the_servers_message(fake_github, make_context, logs):
    fake_github.add_pull(7, diff=DIFF)
    outcome = review_pull(make_context(model=live_model("missing_model", name="stratum-e2e-no-such-model:latest", timeout=60)), 7)
    assert outcome.status == "published"
    body = fake_github.summary_comments(7)[0]["body"]
    assert "The model didn't answer, so this review has only the checks" in body
    assert "404" in body or "not found" in body.lower(), body
    assert_contract(fake_github, number=7, head=head_of(7), logs=logs)


def test_local_review_with_the_real_model(tmp_path):
    repo = tmp_path / "repo"
    repo.mkdir()
    env = {**os.environ, "GIT_CONFIG_GLOBAL": str(tmp_path / "gitconfig"), "GIT_CONFIG_NOSYSTEM": "1", "GIT_AUTHOR_NAME": "a", "GIT_AUTHOR_EMAIL": "a@x.y",
           "GIT_COMMITTER_NAME": "a", "GIT_COMMITTER_EMAIL": "a@x.y"}
    (tmp_path / "gitconfig").write_text("")

    def git(*args):
        subprocess.run(["git", "-C", str(repo), *args], check=True, capture_output=True, env=env)

    git("init", "-q", "-b", "main")
    (repo / "app.py").write_text("import sqlite3\n\ndef get_user(conn, user_id):\n    return conn.execute('SELECT * FROM users WHERE id = ?', (user_id,)).fetchone()\n", encoding="utf-8")
    git("add", ".")
    git("commit", "-q", "-m", "init")
    git("checkout", "-q", "-b", "feature")
    (repo / "app.py").write_text("import sqlite3\nimport os\n\ndef get_user(conn, user_id):\n    row = conn.execute(f\"SELECT * FROM users WHERE id = {user_id}\").fetchone()\n"
                                 "    return row[0]\n\ndef run(cmd):\n    os.system('ls ' + cmd)\n", encoding="utf-8")
    git("commit", "-q", "-am", "Faster user lookup 🚀")
    result = run_stratum(["review", "--local", "main"], env={"STRATUM_MODEL_ENDPOINT": live_endpoint(), "STRATUM_MODEL_NAME": live_model_name()}, cwd=str(repo), timeout=900)
    assert result.returncode == 0, (result.stdout, result.stderr)  # high findings, nothing critical: exit 0
    assert_no_traceback(result.stderr)
    assert "code/sql" in result.stdout and "code/shell" in result.stdout
    assert "What this changes:" in result.stdout, "the model's summary is printed when it answered"
    assert "The model didn't answer" not in result.stderr, result.stderr
