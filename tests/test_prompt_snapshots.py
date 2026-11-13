"""The exact prompts each profile builds for a fixed diff. A change meant for
one profile that alters the other shows up as a snapshot diff in review."""

import json

import pytest
import snapshots
from fakes import FakeModel

from stratum_open.diff import parse_diff
from stratum_open.policy import parse_policy
from stratum_open.profiles import apply
from stratum_open.review import review

DIFF = """diff --git a/app/users.py b/app/users.py
--- a/app/users.py
+++ b/app/users.py
@@ -1,6 +1,7 @@ import sqlite3
 import sqlite3
+import requests

 def get_user(conn, user_id):
-    cur = conn.execute("SELECT * FROM users WHERE id = ?", (user_id,))
+    cur = conn.execute(f"SELECT * FROM users WHERE id = {user_id}")
+    return cur.fetchone()

 def delete_user(conn, user_id):
@@ -20,4 +21,2 @@ def audit():
 x = 1
-y = 2
-z = 3
 w = 4
"""


@pytest.mark.parametrize("profile", ["small", "large"])
def test_prompts(profile):
    model = FakeModel('{"summary": "", "findings": []}')
    policy = apply(parse_policy("review:\n  rules: ['Every SQL query is parameterized.']\n"), profile)
    review(title="Query users", description="Small refactor.", files=parse_diff(DIFF), policy=policy, model=model,
           context=["`app/users.py` is imported by 12 files."])
    system, user = model.prompts[0]
    snapshots.check(f"prompt_{profile}.txt", f"{system}\n\n===== user =====\n\n{user}\n")


def test_large_per_file_and_walkthrough_prompts():
    from stratum_open.findings import Finding
    from stratum_open.review_large import review_files, synthesize

    model = FakeModel(json.dumps({"risk_score": 70, "change_summary": "Builds the query from user_id.", "findings": []}), "{}")
    policy = apply(parse_policy("review:\n  rules: ['Every SQL query is parameterized.']\n  file_reviews: 1\n  lens_files: 0\n"
                                "  parallel_calls: 1\n"), "large")
    large = review_files(title="Query users", description="Small refactor.", files=parse_diff(DIFF), policy=policy, model=model,
                         notes=["`app/users.py` is imported by 12 files."])
    synthesize(large, [Finding("SQL built from strings", "user_id goes straight into the query.", "high", "security", "app/users.py", 6)],
               title="Query users", description="Small refactor.", policy=policy, model=model)
    (file_system, file_user), (synthesis_system, synthesis_user) = model.prompts
    snapshots.check("prompt_large_file.txt", f"{file_system}\n\n===== user =====\n\n{file_user}\n")
    snapshots.check("prompt_large_synthesis.txt", f"{synthesis_system}\n\n===== user =====\n\n{synthesis_user}\n")


def test_the_logic_pass_prompt():
    model = FakeModel('{"summary": "", "findings": []}', '{"decisions": [], "findings": []}')
    policy = apply(parse_policy("review:\n  logic_pass: true\n"), "small")
    review(title="Query users", description="Small refactor.", files=parse_diff(DIFF), policy=policy, model=model)
    system, user = model.prompts[1]
    snapshots.check("prompt_logic.txt", f"{system}\n\n===== user =====\n\n{user}\n")


@pytest.mark.parametrize("inline", [False, True], ids=["top-level", "inline"])
def test_the_ask_prompt(inline):
    from fakes import FakeGitHub

    from stratum_open.github import publish
    from stratum_open.github.commands import handle
    from stratum_open.runner import Context

    gh = FakeGitHub(diff=DIFF, files={("app/users.py", "a" * 40): "".join(f"line {n}\n" for n in range(1, 30))})
    state = publish.State(findings=[{"title": "SQL built from strings", "detail": "user_id goes straight into the query.", "severity": "high",
                                     "path": "app/users.py", "line_start": 6, "suggestion": "Pass it as a parameter.", "fingerprint": "0123456789ab"}])
    gh.issue_comments_[7].append({"id": 1, "body": f"{publish.SUMMARY_MARKER}\n{state.encode()}", "user": {"login": "stratum[bot]"}})
    gh.review_comments_.append({"id": 2, "path": "app/users.py", "line": 6, "user": {"login": "stratum[bot]"},
                                "body": "**High · bug:** SQL built from strings\n\n<!-- stratum:finding 0123456789ab model:sql -->"})
    comment = {"id": 3, "body": "/stratum ask can user_id be a string here?", "user": {"login": "carol", "type": "User"}}
    if inline:
        payload = {"action": "created", "comment": {**comment, "in_reply_to_id": 2}, "pull_request": {"number": 7}}
    else:
        payload = {"action": "created", "comment": comment, "issue": {"number": 7, "pull_request": {}}}
    model = FakeModel("Yes.")
    ctx = Context(gh=gh, policy=parse_policy(None), model=model, bot="stratum[bot]", log=lambda line: None)
    assert handle(ctx, "pull_request_review_comment" if inline else "issue_comment", payload) == "ask"
    [(system, user)] = model.prompts
    snapshots.check(f"prompt_ask{'_inline' if inline else ''}.txt", f"{system}\n\n===== user =====\n\n{user}\n")
