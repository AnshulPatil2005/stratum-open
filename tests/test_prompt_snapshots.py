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
