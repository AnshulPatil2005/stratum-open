import inspect
import json
import sqlite3
import threading
from contextlib import closing
from datetime import datetime, timedelta, timezone

import pytest

from stratum_open.findings import Finding
from stratum_open.storage import FINDING_FIELDS, SCHEMA_VERSION, TABLES, NullStorage, SQLiteStorage, Storage
from stratum_open.util import REDACTED

REPO = "Octo/Widgets"
OTHER = "octo/gadgets"


@pytest.fixture
def storage(tmp_path):
    db = SQLiteStorage(str(tmp_path / "stratum.db"))
    yield db
    db.close()


def finding(title="Off-by-one in loop", rule="", fingerprint="fp1", **extra):
    values = dict(
        detail="The loop stops one short.", severity="high", category="bug", path="src/a.py",
        line_start=10, line_end=12, suggestion="Use <= instead.", replacement="for i in range(n + 1):",
        rule=rule, fingerprint=fingerprint,
    )
    values.update(extra)
    return Finding(title=title, **values)


def later(days):
    return datetime.now(timezone.utc) + timedelta(days=days)


def counts(storage, repo=REPO):
    return {table: len(rows) for table, rows in storage.export(repo).items()}


def test_review_round_trip_keeps_only_the_listed_fields(storage):
    first = finding()
    second = finding(title="Whole-PR note", path="", line_start=None, fingerprint="fp2", severity="low", category="tests")
    storage.record_review(REPO, 7, "abc123", 70, "Two findings.", [first, second])

    stored = storage.latest_findings("octo/widgets", 7)
    assert [set(row) for row in stored] == [set(FINDING_FIELDS)] * 2
    assert stored[0] == {key: getattr(first, key) for key in FINDING_FIELDS}
    assert stored[1]["path"] == "" and stored[1]["line_start"] is None
    assert Finding.from_dict(stored[0]).fingerprint == "fp1"

    held = json.dumps(storage.export(REPO))
    assert first.replacement not in held
    assert first.suggestion not in held
    [review] = storage.export(REPO)["reviews"]
    assert (review["repo"], review["pr"], review["head_sha"], review["risk"], review["summary"]) == ("octo/widgets", 7, "abc123", 70, "Two findings.")
    assert review["created_at"].endswith("Z")


def test_latest_findings_come_from_the_newest_review(storage):
    storage.record_review(REPO, 7, "abc", 70, "", [finding(fingerprint="old")])
    storage.record_review(REPO, 7, "def", 20, "", [finding(fingerprint="new")])
    storage.record_review(REPO, 8, "xyz", 20, "", [finding(fingerprint="other-pr")])
    assert [row["fingerprint"] for row in storage.latest_findings(REPO, 7)] == ["new"]

    storage.record_review(REPO, 7, "ghi", 5, "Nothing found.", [])
    assert storage.latest_findings(REPO, 7) == []
    assert storage.latest_findings(REPO, 99) == []


def test_one_vote_per_voter_the_latest_wins_and_zero_removes(storage):
    storage.record_vote(REPO, "check:eval", "fp1", 101, "Alice", 1)
    storage.record_vote(REPO, "check:eval", "fp1", 101, "alice", -1)
    assert storage.rule_votes(REPO) == {"check:eval": (0, 1)}

    storage.record_vote(REPO, "check:eval", "fp1", 101, "bob", 1)
    storage.record_vote(REPO, "check:eval", "fp2", 102, "alice", 1)  # another comment counts again
    assert storage.rule_votes(REPO) == {"check:eval": (2, 1)}

    storage.record_vote(REPO, "check:eval", "fp1", 101, "ALICE", 0)
    assert storage.rule_votes(REPO) == {"check:eval": (2, 0)}
    storage.record_vote(REPO, "check:eval", "fp1", 101, "carol", 0)  # nothing to remove is fine
    assert storage.rule_votes(OTHER) == {}

    with pytest.raises(ValueError):
        storage.record_vote(REPO, "check:eval", "fp1", 101, "alice", 2)


def test_rules_are_muted_by_enough_thumbs_down(storage):
    def vote(comment_id, rule, up, down):
        for n in range(up + down):
            storage.record_vote(REPO, rule, "fp", comment_id, f"voter{n}", 1 if n < up else -1)

    vote(1, "too-few", 0, 4)
    vote(2, "at-the-rate", 2, 3)
    vote(3, "below-the-rate", 3, 2)
    vote(4, "all-down", 0, 5)
    assert storage.muted_rules(REPO) == {"at-the-rate", "all-down"}
    assert storage.muted_rules(REPO, min_votes=4, down_rate=0.9) == {"too-few", "all-down"}
    assert storage.muted_rules(OTHER) == set()


def test_maintainers_mute_and_unmute_by_hand(storage):
    storage.mute(REPO, "model:style", by="Maintainer")
    assert storage.muted_rules(REPO) == {"model:style"}
    assert storage.export(REPO)["mutes"][0]["muted_by"] == "maintainer"
    assert storage.muted_rules(OTHER) == set()

    storage.unmute(REPO, "model:style")
    assert storage.muted_rules(REPO) == set()


def test_unmuting_a_rule_muted_by_votes_clears_its_votes(storage):
    for n in range(5):
        storage.record_vote(REPO, "noisy", "fp", n, f"voter{n}", -1)
    storage.record_vote(REPO, "kept", "fp", 99, "voter0", -1)
    assert storage.muted_rules(REPO) == {"noisy"}

    storage.unmute(REPO, "noisy")
    assert storage.muted_rules(REPO) == set()
    assert storage.rule_votes(REPO) == {"kept": (0, 1)}


def test_purge_keeps_each_window_and_never_touches_feedback(storage):
    storage.record_review(REPO, 7, "abc", 70, "", [finding(), finding(fingerprint="fp2")])
    storage.record_job(REPO, 7, "pull_request.opened", "reviewed")
    storage.record_vote(REPO, "rule", "fp1", 1, "alice", -1)
    storage.mute(REPO, "muted", by="maintainer")

    assert storage.purge(now=later(13)) == {"reviews": 0, "findings": 0, "jobs": 0}
    assert storage.purge(now=later(15)) == {"reviews": 0, "findings": 0, "jobs": 1}
    assert storage.purge(now=later(179)) == {"reviews": 0, "findings": 0, "jobs": 0}
    assert storage.purge(now=later(181)) == {"reviews": 1, "findings": 2, "jobs": 0}

    assert counts(storage) == {"reviews": 0, "findings": 0, "jobs": 0, "votes": 1, "mutes": 1}
    assert storage.purge(now=later(10_000)) == {"reviews": 0, "findings": 0, "jobs": 0}
    assert storage.rule_votes(REPO) == {"rule": (0, 1)}
    assert storage.muted_rules(REPO) == {"muted"}


def test_purge_takes_other_windows_and_one_repo(storage):
    storage.record_review(REPO, 1, "abc", 5, "", [finding()])
    storage.record_review(OTHER, 1, "abc", 5, "", [finding()])
    storage.record_job(REPO, 1, "push", "reviewed")

    assert storage.purge(findings_days=30, jobs_days=60, now=later(31), repo=REPO) == {"reviews": 1, "findings": 1, "jobs": 0}
    assert counts(storage, OTHER)["reviews"] == 1
    assert storage.purge(findings_days=30, now=later(31).replace(tzinfo=None)) == {"reviews": 1, "findings": 1, "jobs": 1}


def test_prompt_logs_are_kept_only_when_given_and_go_with_their_job(storage):
    storage.record_job(REPO, 7, "pull_request.opened", "reviewed")
    storage.record_job(REPO, 7, "pull_request.synchronize", "failed", error="model timed out", prompt_log="PROMPT\n---\nREPLY")
    storage.record_job(REPO, None, "installation.created", "ok", prompt_log="")

    jobs = storage.export(REPO)["jobs"]
    assert [job["prompt_log"] for job in jobs] == [None, "PROMPT\n---\nREPLY", ""]
    assert jobs[1]["error"] == "model timed out"
    assert jobs[2]["pr"] is None

    storage.purge(now=later(15))
    assert storage.export(REPO)["jobs"] == []


def test_token_shaped_text_never_reaches_the_database(storage):
    key = "-----BEGIN RSA PRIVATE KEY-----\nMIIEow\n-----END RSA PRIVATE KEY-----"
    secrets = [
        "ghs_" + "a1" * 18, "github_pat_" + "B2_" * 10, "sk-ant-" + "x9" * 12,
        "eyJhbGciOiJSUzI1NiJ9.eyJpc3MiOiIxMjM0In0.c2lnbmF0dXJlLXZhbHVl", key,
    ]
    for secret in secrets:
        storage.record_job(REPO, 1, "push", "failed", error=f"401 using {secret} on retry", prompt_log=f"diff\n{secret}\n")
    storage.record_job(REPO, 1, "push", "failed", error="Authorization: Bearer abcdefghijklmnop1234 rejected")
    storage.record_job(REPO, 1, "push", "failed", error="GET https://api.example.com/v1?api_key=hunter2hunter2&x=1")
    storage.record_review(REPO, 1, "abc", 5, f"summary {secrets[0]}", [finding(detail=f"Hard-coded token {secrets[1]}.")])

    held = json.dumps(storage.export(REPO))
    for secret in secrets + ["abcdefghijklmnop1234", "hunter2hunter2", "MIIEow"]:
        assert secret not in held
    jobs = storage.export(REPO)["jobs"]
    assert jobs[0]["error"] == f"401 using {REDACTED} on retry"
    assert jobs[-1]["error"].endswith("&x=1")
    assert storage.latest_findings(REPO, 1)[0]["detail"] == f"Hard-coded token {REDACTED}."


def test_delete_pr_removes_that_prs_rows_only(storage):
    storage.record_review(REPO, 7, "abc", 70, "", [finding(), finding(fingerprint="fp2")])
    storage.record_review(REPO, 7, "def", 70, "", [finding()])
    storage.record_job(REPO, 7, "pull_request.opened", "reviewed", prompt_log="code")
    storage.record_review(REPO, 8, "abc", 70, "", [finding()])
    storage.record_job(REPO, 8, "pull_request.opened", "reviewed")
    storage.record_review(OTHER, 7, "abc", 70, "", [finding()])
    storage.record_vote(REPO, "rule", "fp1", 1, "alice", 1)

    assert storage.delete_pr("OCTO/widgets", 7) == 2 + 3 + 1
    assert storage.latest_findings(REPO, 7) == []
    assert counts(storage) == {"reviews": 1, "findings": 1, "jobs": 1, "votes": 1, "mutes": 0}
    assert counts(storage, OTHER)["findings"] == 1
    assert storage.delete_pr(REPO, 7) == 0


def test_delete_feedback_removes_votes_and_mutes_only(storage):
    for repo in (REPO, OTHER):
        storage.record_review(repo, 1, "abc", 5, "", [finding()])
        storage.record_vote(repo, "rule", "fp1", 1, "alice", 1)
        storage.record_vote(repo, "rule", "fp1", 1, "bob", -1)
        storage.mute(repo, "muted", by="maintainer")

    assert storage.delete_feedback(REPO) == 3
    assert counts(storage) == {"reviews": 1, "findings": 1, "jobs": 0, "votes": 0, "mutes": 0}
    assert counts(storage, OTHER)["votes"] == 2
    assert storage.delete_feedback() == 3
    assert counts(storage, OTHER) == {"reviews": 1, "findings": 1, "jobs": 0, "votes": 0, "mutes": 0}


def test_delete_repo_removes_every_row_for_the_repo(storage):
    for repo in (REPO, OTHER):
        storage.record_review(repo, 1, "abc", 5, "", [finding(), finding(fingerprint="fp2")])
        storage.record_job(repo, 1, "push", "reviewed", prompt_log="p")
        storage.record_vote(repo, "rule", "fp1", 1, "alice", 1)
        storage.mute(repo, "muted", by="maintainer")

    assert storage.delete_repo("octo/WIDGETS") == 6
    assert counts(storage) == {table: 0 for table in TABLES}
    assert counts(storage, OTHER) == {"reviews": 1, "findings": 2, "jobs": 1, "votes": 1, "mutes": 1}


def test_export_covers_every_table(tmp_path):
    path = str(tmp_path / "stratum.db")
    SQLiteStorage(path).close()
    with closing(sqlite3.connect(path)) as raw:
        tables = {row[0] for row in raw.execute("SELECT name FROM sqlite_master WHERE type = 'table' AND name NOT LIKE 'sqlite_%'")}
    storage = SQLiteStorage(path)
    assert set(storage.export(REPO)) == tables == set(TABLES)
    storage.close()


def test_the_file_reopens_with_its_rows_and_refuses_a_newer_schema(tmp_path):
    path = str(tmp_path / "stratum.db")
    first = SQLiteStorage(path)
    first.record_review(REPO, 1, "abc", 5, "kept", [finding()])
    first.close()

    again = SQLiteStorage(path)
    assert again.latest_findings(REPO, 1)[0]["fingerprint"] == "fp1"
    again.close()

    with closing(sqlite3.connect(path)) as raw:
        assert raw.execute("PRAGMA journal_mode").fetchone()[0] == "wal"
        assert raw.execute("PRAGMA user_version").fetchone()[0] == SCHEMA_VERSION
        raw.execute("PRAGMA user_version = 99")
    with pytest.raises(RuntimeError, match="schema version 99"):
        SQLiteStorage(path)


def test_threads_can_share_one_storage(storage):
    def vote(start):
        for n in range(start, start + 25):
            storage.record_vote(REPO, "rule", "fp", n, "alice", -1)

    threads = [threading.Thread(target=vote, args=(start,)) for start in range(0, 100, 25)]
    for thread in threads:
        thread.start()
    for thread in threads:
        thread.join()
    assert storage.rule_votes(REPO) == {"rule": (0, 100)}


def _protocol_methods():
    return [name for name, value in vars(Storage).items() if inspect.isfunction(value) and not name.startswith("_")]


@pytest.mark.parametrize("cls", [NullStorage, SQLiteStorage])
def test_storages_match_the_protocol(cls):
    names = _protocol_methods()
    assert len(names) == 13
    for name in names:
        assert inspect.signature(getattr(cls, name)) == inspect.signature(getattr(Storage, name)), name


def test_null_storage_stores_nothing():
    null = NullStorage()
    null.record_review(REPO, 1, "abc", 5, "", [finding()])
    null.record_job(REPO, 1, "push", "reviewed", prompt_log="p")
    null.record_vote(REPO, "rule", "fp", 1, "alice", -1)
    null.mute(REPO, "rule", by="maintainer")
    null.unmute(REPO, "rule")
    assert null.rule_votes(REPO) == {}
    assert null.muted_rules(REPO) == set()
    assert null.latest_findings(REPO, 1) == []
    assert null.purge() == {"reviews": 0, "findings": 0, "jobs": 0}
    assert null.delete_pr(REPO, 1) == null.delete_feedback() == null.delete_repo(REPO) == 0
    assert null.export(REPO) == {table: [] for table in TABLES}
