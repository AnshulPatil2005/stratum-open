"""`engine.analyze` called the way an embedder (Stratum) calls it: its own
settings, its own findings, and its hooks."""

import json
from dataclasses import replace

from fakes import FakeModel

from stratum_open import Finding, analyze, parse_diff, parse_policy
from stratum_open.engine import snippet_of
from stratum_open.github.publish import state_finding
from stratum_open.profiles import ModelInfo

DIFF = """diff --git a/app/users.py b/app/users.py
--- a/app/users.py
+++ b/app/users.py
@@ -1,4 +1,6 @@
 import sqlite3
+import requests

 def get_user(conn, user_id):
-    cur = conn.execute("SELECT * FROM users WHERE id = ?", (user_id,))
+    cur = conn.execute(f"SELECT * FROM users WHERE uid = {user_id}")
+    return cur.fetchone()
"""
FILES = parse_diff(DIFF)


def reply(*findings):
    return json.dumps({"summary": "Changes the query.", "matches_description": "yes", "findings": list(findings)})


UNKNOWN_ID = {"file": "app/users.py", "code": "return cur.fetchone()", "severity": "medium", "category": "bug",
              "title": "None for unknown ids", "detail": "fetchone returns None and callers index it.", "fix": "Check for None."}


def run(model=None, **kwargs):
    return analyze(title="Query users", description="Small refactor.", files=FILES, policy=kwargs.pop("policy", parse_policy(None)),
                   model=model, **kwargs)


def test_checks_only_without_a_model():
    result = run()
    assert result.review is None and result.profile == "small" and result.complete
    assert {f.rule for f in result.findings} >= {"code/sql"}
    assert result.conclusion == "neutral"  # the SQL check is high


def test_the_model_review_merges_with_the_checks_and_reports_the_profile():
    class Big(FakeModel):
        def describe(self):
            return ModelInfo(billions=120, context=200_000, local=False)

    model = Big(reply(UNKNOWN_ID))
    result = run(model)
    assert result.profile == "large" and result.profile_why == "120B parameters"
    assert result.policy.review.diff_format == "split" and model.context_tokens == 32768
    assert {"None for unknown ids"} <= {f.title for f in result.findings}
    assert result.engine_version == __import__("stratum_open").__version__


def test_hooks_replace_the_defaults():
    def weigh(findings):
        return [replace(f, severity="critical") if f.rule == "code/sql" else f for f in findings], 7

    seen = {}

    def score(findings, bonus):
        seen["bonus"] = bonus
        return 42

    result = run(FakeModel(reply()), weigh=weigh, score=score, conclusion=lambda findings, total, complete: f"custom:{total}")
    assert seen["bonus"] == 7 and result.score == 42 and result.conclusion == "custom:42"
    assert any(f.severity == "critical" and f.rule == "code/sql" for f in result.findings)


def test_extra_findings_guidance_and_exclusions():
    model = FakeModel(reply(UNKNOWN_ID))
    mine = Finding("Calls a service it shouldn't", "The billing service is off limits here.", "high", "breaking-change",
                   "app/users.py", 2, source="check", rule="stratum/contracts")
    result = run(model, extra_findings=[mine], guidance="We always check fetchone() for None.", exclude_rules={"code/sql"})
    system, _ = model.prompts[0]
    assert "We always check fetchone() for None." in system
    rules = {f.rule for f in result.findings}
    assert "stratum/contracts" in rules and "code/sql" not in rules
    assert [f.rule for f in result.excluded] == ["code/sql"]


def test_previous_findings_carry_over_on_an_incremental_review():
    first = run(FakeModel(reply(UNKNOWN_ID)))
    prior = [state_finding(f, snippet_of(f, FILES)) for f in first.findings if f.source == "model"]
    model = FakeModel()  # nothing new for the model to read
    again = run(model, only_paths=set(), previous=prior)
    assert model.prompts == [] and again.review is None
    assert "None for unknown ids" in {f.title for f in again.findings}
