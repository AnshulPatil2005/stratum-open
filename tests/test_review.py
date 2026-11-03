import json

from fakes import FakeModel

from stratum_open.diff import parse_diff
from stratum_open.model import Reply, parse_json
from stratum_open.policy import parse_policy
from stratum_open.review import review

DIFF = """diff --git a/app/users.py b/app/users.py
--- a/app/users.py
+++ b/app/users.py
@@ -1,4 +1,6 @@
 import sqlite3
+import requests

 def get_user(conn, user_id):
-    cur = conn.execute("SELECT * FROM users WHERE id = ?", (user_id,))
+    cur = conn.execute(f"SELECT * FROM users WHERE id = {user_id}")
+    return cur.fetchone()
"""


def answer(**overrides):
    data = {
        "summary": "Switches the user query to an f-string.",
        "matches_description": "yes",
        "mismatch": "",
        "findings": [{
            "file": "app/users.py", "code": 'cur = conn.execute(f"SELECT * FROM users WHERE id = {user_id}")', "line": 2, "end_line": 2,
            "severity": "high", "category": "security", "title": "SQL injection", "detail": "user_id goes into the SQL text.",
            "fix": "Use a parameter.", "replacement": '    cur = conn.execute("SELECT * FROM users WHERE id = ?", (user_id,))',
        }],
    }
    data.update(overrides)
    return json.dumps(data)


def run(model, policy=None, **kwargs):
    return review(title="Query users", description="Small refactor.", files=parse_diff(DIFF), policy=policy or parse_policy(None), model=model, **kwargs)


def test_the_quoted_code_decides_the_line_not_the_number():
    result = run(FakeModel(answer()))
    [finding] = result.findings
    assert (finding.path, finding.line_start, finding.line_end) == ("app/users.py", 5, 5)
    assert finding.replacement.startswith("    cur = conn.execute(\"SELECT")
    assert result.summary.startswith("Switches")
    assert result.complete and result.ran


def test_without_a_quote_a_replacement_is_never_offered():
    data = json.loads(answer())
    del data["findings"][0]["code"]
    data["findings"][0]["line"] = 5
    [finding] = run(FakeModel(json.dumps(data))).findings
    assert finding.line_start == 5 and finding.replacement is None


def test_bad_parts_of_a_reply_are_dropped_not_passed_on():
    findings = [
        {"file": "nope.py", "title": "x", "detail": "not in the diff"},
        {"file": "app/users.py", "title": "", "detail": "no title"},
        {"file": "users.py", "title": "Suffix match", "detail": "matched by suffix", "line": "6", "severity": "URGENT", "category": "bug risk"},
        "not an object",
    ]
    result = run(FakeModel(answer(findings=findings)))
    [finding] = result.findings
    assert (finding.path, finding.line_start, finding.severity, finding.category) == ("app/users.py", 6, "medium", "bug")
    assert result.dropped == 3


def test_a_reply_that_isnt_json_counts_as_a_failed_call():
    result = run(FakeModel("I think the code looks fine!"))
    assert result.failed_calls == 1 and not result.ran and result.findings == []

    down = run(FakeModel(Reply("", "m", 0, error="couldn't reach the model")))
    assert not down.ran and down.calls[0]["error"] == "couldn't reach the model"


def test_project_rules_and_history_go_in_the_prompt():
    model = FakeModel(answer(findings=[]))
    policy = parse_policy("review:\n  rules: ['Every query is parameterized.']\n")
    run(model, policy, context=["app/users.py is imported by 12 files."], hotspots={"app/users.py"})
    system, user = model.prompts[0]
    assert "Every query is parameterized." in system
    assert "imported by 12 files" in system and "review them strictly: app/users.py" in system
    assert "     5 +      cur = conn.execute" in user


def test_ignored_paths_and_only_paths():
    model = FakeModel()
    result = run(model, parse_policy("review:\n  ignore_paths: ['app/**']\n"))
    assert result.calls == [] and model.prompts == []
    assert run(FakeModel(), only_paths={"other.py"}).calls == []


def test_the_worst_description_answer_wins_across_chunks():
    big = "".join(
        f"diff --git a/f{i}.py b/f{i}.py\n--- a/f{i}.py\n+++ b/f{i}.py\n@@ -1 +1,40 @@\n x\n" + "".join(f"+line {n} " * 20 + "\n" for n in range(40))
        for i in range(2)
    )
    model = FakeModel(answer(findings=[], matches_description="yes"), answer(findings=[], matches_description="no", mismatch="It also deletes users."))
    policy = parse_policy("review:\n  context_tokens: 4000\n")
    result = review(title="t", description="d", files=parse_diff(big), policy=policy, model=model)
    assert len(result.calls) == 2
    assert (result.matches_description, result.mismatch) == ("no", "It also deletes users.")


def test_parse_json_copes_with_what_small_models_send():
    assert parse_json('```json\n{"a": 1}\n```') == {"a": 1}
    assert parse_json('Here you go: {"a": [1, 2,],} Hope that helps!') == {"a": [1, 2]}
    assert parse_json("no json here") is None


def test_a_repeated_vague_finding_becomes_one():
    lines = "".join(f"+    run_{n}()\n" for n in range(6))
    diff = f"diff --git a/ci.py b/ci.py\n--- a/ci.py\n+++ b/ci.py\n@@ -1 +1,7 @@\n x = 1\n{lines}"
    same = [{"file": "ci.py", "code": f"run_{n}()", "severity": "medium", "category": "security",
             "title": "Potential for insecure execution", "detail": "Could be risky."} for n in range(5)]
    other = [{"file": "ci.py", "code": f"run_5()", "severity": "high", "category": "bug", "title": f"Bug {n}", "detail": "d"} for n in range(4)]
    result = review(title="t", description="d", files=parse_diff(diff), policy=parse_policy(None), model=FakeModel(answer(findings=same + other)))
    titles = [f.title for f in result.findings]
    assert len(titles) == 3 and titles.count("Potential for insecure execution") <= 1
    vague = [f for f in result.findings if f.title.startswith("Potential")]
    assert not vague or "The same applies to lines" in vague[0].detail


def test_narration_and_low_findings_are_not_shown_by_default():
    narrated = {"file": "app/users.py", "code": "return cur.fetchone()", "severity": "medium", "category": "maintainability",
                "title": "New function", "detail": "A function was added."}
    low = {"file": "app/users.py", "code": "import requests", "severity": "low", "category": "bug",
           "title": "Unused import", "detail": "requests isn't used here."}
    result = run(FakeModel(answer(findings=[narrated, low])))
    assert result.findings == [] and result.dropped == 2
    shown_low = run(FakeModel(answer(findings=[narrated, low])), parse_policy("review:\n  min_severity: low\n"))
    assert [f.title for f in shown_low.findings] == ["Unused import"]


def test_a_reply_cut_off_mid_answer_keeps_the_findings_before_the_cut():
    full = json.loads(answer())
    finding = json.dumps(full["findings"][0])
    cut = '{"summary": "Changes the query.", "matches_description": "yes", "findings": [' + finding + ', {"file": "app/users.py", "tit'
    result = run(FakeModel(cut))
    assert result.ran and result.failed_calls == 0 and result.salvaged_calls == 1
    assert [f.title for f in result.findings] == ["SQL injection"]
    assert result.summary == "Changes the query."
    assert result.calls[0]["salvaged"] is True


def test_near_json_and_mojibake_are_read():
    from stratum_open.model import parse_json, repair_text, salvage

    assert parse_json("Sure! Here is the review:\n```json\n{\"a\": 1}\n```\nLet me know.") == {"a": 1}
    assert parse_json("{'summary': 'ok', findings: []}") == {"summary": "ok", "findings": []}
    assert repair_text("low\u00e2\u20ac\u2018severity \ufffd") == "low\u2011severity "
    assert salvage("nothing useful") is None
