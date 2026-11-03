"""H. The GitHub App: webhook verification and parsing, the job queue, the
database, retention, and startup checks."""

from __future__ import annotations

import hashlib
import hmac
import io
import json
import sqlite3
import threading
import time
from datetime import datetime, timedelta, timezone

import pytest

from stratum_open.app import AppAuth, Server, from_env
from stratum_open.findings import Finding
from stratum_open.github.feedback import collect_votes
from stratum_open.storage import SQLiteStorage

from conftest import DIFF, SQL_LINE, head_of
from contract import assert_contract, run_stratum
from fake_model import Response, finding, json_reply

HEAD = head_of(7)
SECRET = b"s3cret"


class StubAuth:
    api = "https://api.github.invalid"

    def bot_login(self):
        return "stratum[bot]"


def make_server(tmp_path, monkeypatch, storage=None, **kwargs) -> Server:
    monkeypatch.setattr("threading.Thread.start", lambda self: None)  # no workers
    return Server(StubAuth(), SECRET.decode(), storage or SQLiteStorage(str(tmp_path / "s.db")), str(tmp_path / "repos"), env={}, log=kwargs.pop("log", lambda line: None), **kwargs)


def call(server, body: bytes, *, event="pull_request", secret=SECRET, path="/webhook", signature=None, length=None, delivery="d-1"):
    sig = signature if signature is not None else "sha256=" + hmac.new(secret, body, hashlib.sha256).hexdigest()
    environ = {"PATH_INFO": path, "REQUEST_METHOD": "POST", "CONTENT_LENGTH": str(len(body)) if length is None else length,
               "wsgi.input": io.BytesIO(body), "HTTP_X_HUB_SIGNATURE_256": sig, "HTTP_X_GITHUB_EVENT": event, "HTTP_X_GITHUB_DELIVERY": delivery}
    status: list[str] = []
    response = b"".join(server(environ, lambda s, headers: status.append(s)))
    return status[0], json.loads(response)


def pr_event(number=7, action="synchronize", **extra):
    return json.dumps({"action": action, "installation": {"id": 1}, "repository": {"full_name": "acme/widget", "default_branch": "main"},
                       "pull_request": {"number": number, "draft": False, "head": {"sha": HEAD}}, **extra}).encode()


@pytest.mark.parametrize("kwargs, expected", [
    ({"signature": ""}, "401 Unauthorized"), ({"signature": "sha256=deadbeef"}, "401 Unauthorized"), ({"secret": b"wrong"}, "401 Unauthorized"),
    ({"signature": "sha1=abc"}, "401 Unauthorized"), ({}, "202 Accepted"),
])
def test_webhook_signature(tmp_path, monkeypatch, kwargs, expected):
    server = make_server(tmp_path, monkeypatch)
    assert call(server, pr_event(), **kwargs)[0] == expected
    assert server._queue.qsize() == (1 if expected.startswith("202") else 0)


def test_a_server_with_no_secret_accepts_nothing(tmp_path, monkeypatch):
    server = make_server(tmp_path, monkeypatch)
    server.secret = b""
    assert call(server, pr_event(), secret=b"")[0] == "401 Unauthorized"


@pytest.mark.parametrize("body, event, expected", [
    (b"not json", "pull_request", "400 Bad Request"), (b"[1, 2]", "pull_request", "400 Bad Request"), (b'"text"', "pull_request", "400 Bad Request"),
    (b"null", "pull_request", "400 Bad Request"), (b"{}", "pull_request", "202 Accepted"), (b"{}", "star", "202 Accepted"),
    (json.dumps({"action": "opened", "installation": {"id": 1}, "repository": {"full_name": "acme/widget"}}).encode(), "pull_request", "202 Accepted"),
    (json.dumps({"action": "opened", "installation": {"id": 1}, "repository": {"full_name": "acme/widget"}, "pull_request": None}).encode(), "pull_request", "202 Accepted"),
    (json.dumps({"action": "opened", "installation": "1", "repository": "acme/widget", "pull_request": [7]}).encode(), "pull_request", "202 Accepted"),
    (json.dumps({"action": "created", "installation": {"id": 1}, "repository": {"full_name": "acme/widget"}, "issue": {"number": 7, "pull_request": {}},
                 "comment": {"id": 9, "body": None}}).encode(), "issue_comment", "202 Accepted"),
    (json.dumps({"action": "created", "installation": {"id": 1}, "repository": {"full_name": "acme/widget"}, "issue": {"number": 7, "pull_request": {}},
                 "comment": None}).encode(), "issue_comment", "202 Accepted"),
    (json.dumps({"action": "deleted", "installation": {"id": 1}, "repositories": [None, {"name": "x"}, {"full_name": "acme/gone"}]}).encode(), "installation", "202 Accepted"),
], ids=["not-json", "list", "string", "null", "empty-object", "unknown-event", "missing-pr", "null-pr", "wrong-types", "null-comment-body", "null-comment", "odd-uninstall"])
def test_malformed_payloads_never_give_a_500(tmp_path, monkeypatch, body, event, expected):
    logs: list[str] = []
    server = make_server(tmp_path, monkeypatch, log=logs.append)
    status, data = call(server, body, event=event)
    assert status == expected, (status, data)
    if status.startswith("202"):
        assert data == {"queued": False}


def test_bad_content_length_and_oversized_bodies(tmp_path, monkeypatch):
    server = make_server(tmp_path, monkeypatch)
    assert call(server, pr_event(), length="abc")[0] == "400 Bad Request"
    assert call(server, pr_event(), length="0")[0] == "413 Payload Too Large"
    assert call(server, pr_event(), length=str(30 * 1024 * 1024))[0] == "413 Payload Too Large"


def test_replayed_and_racing_deliveries_queue_one_job(tmp_path, monkeypatch):
    server = make_server(tmp_path, monkeypatch)
    for _ in range(3):
        call(server, pr_event(action="opened"), delivery="same")
    call(server, pr_event(action="synchronize"), delivery="later")
    assert server._queue.qsize() == 1 and server._pending[("acme/widget", 7, "review")][1]["action"] == "synchronize"
    comment = json.dumps({"action": "created", "installation": {"id": 1}, "repository": {"full_name": "acme/widget"}, "issue": {"number": 7, "pull_request": {}},
                          "comment": {"id": 9, "body": "@stratum triage"}}).encode()
    for n in range(50):  # each with its own delivery id, so it's the queue's per-comment key doing the collapsing
        call(server, comment, event="issue_comment", delivery=f"comment-{n}")
    assert server._queue.qsize() == 2, "fifty copies of one comment are one job"


class BrokenStorage(SQLiteStorage):
    def record_job(self, *args, **kwargs):
        raise sqlite3.OperationalError("database is locked")


def test_a_worker_survives_a_job_that_fails_and_a_database_that_fails_too(tmp_path):
    logs: list[str] = []
    storage = BrokenStorage(str(tmp_path / "s.db"))
    server = Server(StubAuth(), SECRET.decode(), storage, str(tmp_path / "repos"), env={}, workers=1, log=logs.append)
    runs: list[str] = []

    def failing_run(event, payload):
        runs.append(event)
        raise RuntimeError("the review blew up with token ghp_" + "S3cretS3cretS3cretS3cretS3cretS3cret")

    server.run = failing_run  # type: ignore[method-assign]
    for n in (1, 2, 3):
        call(server, pr_event(number=n), delivery=str(n))
    deadline = time.monotonic() + 10
    while len(runs) < 3 and time.monotonic() < deadline:
        time.sleep(0.05)
    assert len(runs) == 3, "the worker thread died after the first failure"
    assert any("couldn't record the failed job either" in line for line in logs)
    assert "ghp_S3cret" not in "\n".join(logs) and "[redacted]" in "\n".join(logs)


def test_retention_purges_findings_after_180_days_and_jobs_after_14(tmp_path):
    storage = SQLiteStorage(str(tmp_path / "s.db"))
    now = datetime(2026, 11, 3, tzinfo=timezone.utc)
    storage.record_review("acme/widget", 7, HEAD, 50, "s", [Finding("t", "d", path="x.py")])
    storage.record_job("acme/widget", 7, "review", "published")
    with storage._tx() as db:
        db.execute("UPDATE reviews SET created_at = ?", ((now - timedelta(days=181)).strftime("%Y-%m-%dT%H:%M:%SZ"),))
        db.execute("UPDATE jobs SET created_at = ?", ((now - timedelta(days=15)).strftime("%Y-%m-%dT%H:%M:%SZ"),))
    storage.record_review("acme/widget", 8, HEAD, 50, "s", [Finding("t2", "d", path="y.py")])
    deleted = storage.purge(findings_days=180, jobs_days=14, now=now)
    assert deleted == {"reviews": 1, "findings": 1, "jobs": 1}
    assert storage.latest_findings("acme/widget", 8) and not storage.latest_findings("acme/widget", 7)


def test_uninstall_deletes_the_repos_rows_at_once(tmp_path, monkeypatch):
    server = make_server(tmp_path, monkeypatch)
    server.storage.record_review("acme/widget", 7, HEAD, 50, "s", [Finding("t", "d", path="x.py")])
    server.storage.record_vote("acme/widget", "model:t", "f" * 12, 1, "mona", -1)
    server.storage.record_review("other/repo", 1, HEAD, 50, "s", [Finding("t", "d", path="x.py")])
    body = json.dumps({"action": "deleted", "installation": {"id": 1}, "repositories": [{"full_name": "acme/widget"}]}).encode()
    assert call(server, body, event="installation")[0] == "202 Accepted"
    assert server.storage.export("acme/widget") == {t: [] for t in ("reviews", "findings", "jobs", "votes", "mutes")}
    assert server.storage.latest_findings("other/repo", 1)


def test_stratum_data_shows_and_deletes(tmp_path):
    db = str(tmp_path / "s.db")
    storage = SQLiteStorage(db)
    storage.record_review("acme/widget", 7, HEAD, 50, "summary", [Finding("t", "d", path="x.py")])
    storage.record_vote("acme/widget", "model:t", "f" * 12, 1, "mona", -1)
    storage.close()
    shown = run_stratum(["data", "export", "--db", db, "--repo", "acme/widget"], env={})
    assert shown.returncode == 0 and json.loads(shown.stdout)["reviews"][0]["summary"] == "summary"
    deleted = run_stratum(["data", "delete", "--db", db, "--repo", "acme/widget", "--all"], env={})
    assert deleted.returncode == 0 and "Deleted 3 row(s)" in deleted.stdout
    missing = run_stratum(["data", "export", "--db", str(tmp_path / "nope.db"), "--repo", "acme/widget"], env={})
    assert missing.returncode == 1 and "Only the GitHub App stores data" in missing.stderr and "Traceback" not in missing.stderr


def test_votes_from_users_without_write_access_are_ignored(fake_github, make_context, fake_model, tmp_path):
    fake_github.add_pull(7, diff=DIFF)
    fake_github.permissions.update({"mona": "write", "erin": "read"})
    fake_model.queue(Response.ok(json_reply([finding("app/users.py", SQL_LINE, line=5)])))
    storage = SQLiteStorage(str(tmp_path / "s.db"))
    ctx = make_context(storage=storage, feedback=True)
    from stratum_open.runner import review_pull
    review_pull(ctx, 7)
    comment = next(c for c in fake_github.inline_comments(7) if "Unknown id" in c["body"])
    comment["reactions"] = {"+1": 1, "-1": 2}
    fake_github.reactions[comment["id"]] = [{"content": "-1", "user": {"login": "mona"}}, {"content": "-1", "user": {"login": "erin"}},
                                           {"content": "+1", "user": {"login": "ghost"}}, {"content": "heart", "user": {"login": "mona"}}, {"content": "-1", "user": None}]
    assert collect_votes(ctx.gh, 7, fake_github.bot, storage) == 1
    assert storage.rule_votes("acme/widget") == {"model:unknown-id": (0, 1)}


@pytest.mark.parametrize("env, says", [
    ({}, "Missing settings: STRATUM_APP_ID, STRATUM_WEBHOOK_SECRET, STRATUM_APP_PRIVATE_KEY"),
    ({"STRATUM_APP_ID": "1", "STRATUM_WEBHOOK_SECRET": "s", "STRATUM_APP_PRIVATE_KEY_FILE": "/nope/key.pem"}, "Couldn't read STRATUM_APP_PRIVATE_KEY_FILE"),
    ({"STRATUM_APP_ID": "1", "STRATUM_WEBHOOK_SECRET": "s", "STRATUM_APP_PRIVATE_KEY": "not a key"}, "doesn't look like a PEM private key"),
    ({"STRATUM_APP_ID": "1", "STRATUM_WEBHOOK_SECRET": "s", "STRATUM_APP_PRIVATE_KEY": "-----BEGIN RSA PRIVATE KEY-----\nx\n-----END RSA PRIVATE KEY-----", "STRATUM_WORKERS": "two"}, "STRATUM_WORKERS must be a whole number"),
])
def test_bad_settings_fail_fast_with_a_readable_line(env, says, tmp_path, monkeypatch):
    monkeypatch.setattr("threading.Thread.start", lambda self: None)
    with pytest.raises(SystemExit) as stop:
        from_env({**env, "STRATUM_DB": str(tmp_path / "s.db")})
    assert says in str(stop.value)


def test_unusable_database_fails_fast(tmp_path, monkeypatch):
    monkeypatch.setattr("threading.Thread.start", lambda self: None)
    base = {"STRATUM_APP_ID": "1", "STRATUM_WEBHOOK_SECRET": "s", "STRATUM_APP_PRIVATE_KEY": "-----BEGIN RSA PRIVATE KEY-----\nx\n-----END RSA PRIVATE KEY-----"}
    newer = str(tmp_path / "newer.db")
    conn = sqlite3.connect(newer)
    conn.execute("PRAGMA user_version = 99")
    conn.close()
    with pytest.raises(SystemExit) as stop:
        from_env({**base, "STRATUM_DB": newer})
    assert "schema version 99" in str(stop.value)
    with pytest.raises(SystemExit) as stop:
        from_env({**base, "STRATUM_DB": str(tmp_path / "no" / "such" / "dir" / "s.db")})
    assert "Couldn't open the database" in str(stop.value)


def test_the_whole_app_flow_over_http_against_the_fakes(fake_github, fake_model, tmp_path, monkeypatch):
    """A signed webhook reaches a real worker thread, which reviews the PR on
    the fake GitHub with the fake model and records the job."""
    try:
        from cryptography.hazmat.primitives import serialization
        from cryptography.hazmat.primitives.asymmetric import rsa
    except ImportError:  # pragma: no cover
        pytest.skip("cryptography (the `app` extra) isn't installed")
    key = rsa.generate_private_key(public_exponent=65537, key_size=2048).private_bytes(
        serialization.Encoding.PEM, serialization.PrivateFormat.TraditionalOpenSSL, serialization.NoEncryption()).decode()
    fake_github.add_pull(7, diff=DIFF)
    fake_model.queue(Response.ok(json_reply([finding("app/users.py", SQL_LINE, line=5)])))
    logs: list[str] = []
    monkeypatch.setattr(Server, "checkout", lambda self, repo, token, branch, window_days: None)  # no clone of github.com in a test
    server = Server(AppAuth("1", key, api=fake_github.url), SECRET.decode(), SQLiteStorage(str(tmp_path / "s.db")), str(tmp_path / "repos"),
                    env={"STRATUM_MODEL_ENDPOINT": fake_model.url}, workers=1, log=logs.append)
    status, data = call(server, pr_event(action="opened"))
    assert (status, data) == ("202 Accepted", {"queued": True})
    deadline = time.monotonic() + 30
    while not fake_github.check_runs and time.monotonic() < deadline:
        time.sleep(0.05)
    assert_contract(fake_github, number=7, head=HEAD, logs=logs)
    assert {f["title"] for f in server.storage.latest_findings("acme/widget", 7)} >= {"Unknown id"}
    assert server.storage.export("acme/widget")["jobs"][-1]["outcome"] == "published"
    assert fake_github.requests_to("POST", r"/app/installations/1/access_tokens")
