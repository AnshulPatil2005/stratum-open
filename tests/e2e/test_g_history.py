"""G. Git history: track records, hotspots and ownership on every shape of
clone. Short or odd history means fewer notes, never a failed review."""

from __future__ import annotations

import os
import subprocess
from datetime import datetime, timedelta, timezone
from pathlib import Path

import pytest

from stratum_open.repo.history import recent_commits
from stratum_open.repo.hotspots import hotspots
from stratum_open.repo.ownership import owners
from stratum_open.runner import record_for, review_pull
from stratum_open.track_record import build_records

from conftest import DIFF, head_of
from contract import assert_contract

HEAD = head_of(7)


class Repo:
    def __init__(self, path: Path):
        self.path = path
        path.mkdir(parents=True, exist_ok=True)
        self.env = {**os.environ, "GIT_CONFIG_GLOBAL": str(path.parent / "gitconfig"), "GIT_CONFIG_NOSYSTEM": "1",
                    "GIT_AUTHOR_NAME": "a", "GIT_COMMITTER_NAME": "a", "GIT_AUTHOR_EMAIL": "a@users.noreply.github.com", "GIT_COMMITTER_EMAIL": "a@users.noreply.github.com"}
        (path.parent / "gitconfig").write_text("[init]\n\tdefaultBranch = main\n[commit]\n\tgpgsign = false\n")
        self.git("init", "-q", "-b", "main")

    def git(self, *args: str, env: dict | None = None) -> str:
        return subprocess.run(["git", "-C", str(self.path), *args], check=True, capture_output=True, text=True, encoding="utf-8", env={**self.env, **(env or {})}).stdout

    def commit(self, message: str, *, files: dict[str, str] | None = None, email: str | None = None, days_ago: int = 1) -> str:
        for name, text in (files or {"f.py": message}).items():
            (self.path / name).parent.mkdir(parents=True, exist_ok=True)
            (self.path / name).write_text(text, encoding="utf-8")
        self.git("add", "-A")
        when = (datetime.now(timezone.utc) - timedelta(days=days_ago)).isoformat()
        env = {"GIT_AUTHOR_DATE": when, "GIT_COMMITTER_DATE": when}
        if email:
            env |= {"GIT_AUTHOR_EMAIL": email, "GIT_COMMITTER_EMAIL": email}
        self.git("commit", "-q", "--allow-empty", "-m", message, env=env)
        return self.git("rev-parse", "HEAD").strip()


@pytest.fixture
def repo(tmp_path):
    return Repo(tmp_path / "repo")


def test_empty_repo_first_commit_and_no_history(repo):
    assert build_records(str(repo.path)) == {} and recent_commits(str(repo.path)) == [] and hotspots(str(repo.path)) == [] and owners(str(repo.path)) == {}
    repo.commit("first")
    assert build_records(str(repo.path)) == {} and recent_commits(str(repo.path)) == []  # a root commit says nothing about who changes what
    assert build_records(str(repo.path), "no-such-ref") == {}


def test_shallow_clone_detached_head_and_worktree(repo, tmp_path):
    repo.commit("Base", files={"app.py": "x = 0\n"}, days_ago=10)
    for n in range(5):
        repo.commit(f"Change {n} (#{n + 1})", files={"app.py": f"x = {n}\n" + "if x:\n    pass\n" * 3})
    shallow = tmp_path / "shallow"
    subprocess.run(["git", "clone", "-q", "--depth", "1", f"file://{repo.path}", str(shallow)], check=True, env=repo.env, capture_output=True)
    assert isinstance(build_records(str(shallow)), dict)
    assert hotspots(str(shallow)) == []  # one commit of history: nothing to say
    repo.git("checkout", "-q", "HEAD~1")  # detached
    records = build_records(str(repo.path), "HEAD")
    assert sum(len(r.merged) for r in records.values()) == 4
    worktree = tmp_path / "wt"
    repo.git("worktree", "add", "-q", str(worktree), "main")
    assert sum(len(r.merged) for r in build_records(str(worktree), "main").values()) == 5


def test_author_identities_renamed_and_multiple_emails(repo):
    repo.commit("Base", days_ago=10)  # a root commit is never a landed PR
    repo.commit("A (#1)", email="12345+alice@users.noreply.github.com")
    repo.commit("B (#2)", email="alice@example.com")
    repo.commit("C (#3)", email="")
    (repo.path / ".mailmap").write_text("Alice <alice@example.com> <old@example.com>\n")
    repo.commit("D (#4)", email="old@example.com")
    records = build_records(str(repo.path))
    assert {"alice", "alice@example.com"} <= set(records)
    assert sum(len(r.merged) for r in records.values()) == 4


def test_reverts_squashes_merges_and_octopus_merges(repo):
    base = repo.commit("Base", files={"a.py": "a = 1\n"}, days_ago=30)
    landed = repo.commit("Add feature (#10)", files={"a.py": "a = 2\n"}, days_ago=20)
    repo.commit(f'Revert "Add feature (#10)"\n\nThis reverts commit {landed}.', files={"a.py": "a = 1\n"}, days_ago=19)
    repo.commit("Reverts acme/widget#10 again\n\nReverts acme/widget#10", days_ago=18)
    repo.commit("fix: follow-up for #10\n\nFixes #10", days_ago=17)
    repo.commit("Odd revert format: revert of PR 10 (no sha)", days_ago=16)
    for branch in ("b1", "b2", "b3"):
        repo.git("checkout", "-q", "-b", branch, base)
        repo.commit(f"on {branch}", files={f"{branch}.py": "x = 1\n"}, days_ago=15, email=f"{branch}@users.noreply.github.com")
    repo.git("checkout", "-q", "main")
    repo.git("merge", "-q", "--no-ff", "-m", "Merge pull request #11 from acme/b1", "b1", env={"GIT_AUTHOR_DATE": (datetime.now(timezone.utc) - timedelta(days=14)).isoformat()})
    repo.git("merge", "-q", "--no-ff", "-m", "Octopus merge of b2 and b3 (#12)", "b2", "b3", env={"GIT_AUTHOR_DATE": (datetime.now(timezone.utc) - timedelta(days=13)).isoformat()})
    records = build_records(str(repo.path))
    author = records["a"]
    struck = {(s.pr.number, s.kind) for s in author.strikes}
    assert (10, "revert") in struck
    assert 11 in {pr.number for r in records.values() for pr in r.merged}
    top_level = owners(str(repo.path))["."]  # the branch commits by b1, b2 and b3 all touched top-level files
    assert {"b1", "b2", "b3"} <= set(top_level.authors) and top_level.commits >= 3
    assert isinstance(hotspots(str(repo.path)), list)  # `a.py` is edited often but has no branches, so it isn't one


def test_huge_history_past_the_time_limit_is_bounded(fake_github, make_context, logs, repo, monkeypatch):
    repo.commit("x (#1)")
    fake_github.add_pull(7, diff=DIFF)
    import stratum_open.track_record as tr

    def slow(*args, **kwargs):
        raise subprocess.TimeoutExpired(["git", "log"], 120)

    monkeypatch.setattr(tr, "_git", slow)
    ctx = make_context(policy_text="track_record:\n  affects_triage: true\n", checkout=str(repo.path))
    assert record_for(ctx, "a") is None
    assert any("track record: couldn't read the history" in line for line in logs)
    outcome = review_pull(ctx, 7)
    assert outcome.status == "published"
    assert_contract(fake_github, number=7, head=HEAD, logs=logs)


def test_review_with_every_kind_of_checkout(fake_github, make_context, logs, repo, tmp_path):
    fake_github.add_pull(7, diff=DIFF)
    not_a_repo = tmp_path / "plain"
    not_a_repo.mkdir()
    for checkout in (str(repo.path), str(not_a_repo), str(tmp_path / "missing")):
        ctx = make_context(policy_text="track_record:\n  affects_triage: true\n", checkout=checkout)
        outcome = review_pull(ctx, 7)
        assert outcome.status == "published", checkout
    assert_contract(fake_github, number=7, head=HEAD, logs=[l for l in logs if "Traceback" not in l])
    assert not any("Traceback" in l for l in logs)


def test_submodule_in_the_checkout(fake_github, make_context, logs, repo, tmp_path):
    sub = Repo(tmp_path / "sub")
    sub.commit("sub", files={"s.py": "s = 1\n"})
    repo.commit("main (#1)", files={"m.py": "m = 1\n"})
    subprocess.run(["git", "-C", str(repo.path), "-c", "protocol.file.allow=always", "submodule", "add", "-q", f"file://{sub.path}", "vendor/sub"],
                   check=True, env=repo.env, capture_output=True)
    repo.git("commit", "-q", "-m", "Add submodule (#2)")
    fake_github.add_pull(7, diff=DIFF)
    outcome = review_pull(make_context(checkout=str(repo.path)), 7)
    assert outcome.status == "published"
    assert_contract(fake_github, number=7, head=HEAD, logs=logs)
