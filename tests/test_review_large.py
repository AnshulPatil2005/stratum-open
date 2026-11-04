import json
import re
import threading
import time

from fakes import FakeModel

from stratum_open import areas
from stratum_open.diff import parse_diff
from stratum_open.engine import analyze
from stratum_open.github import publish
from stratum_open.model import Reply
from stratum_open.policy import parse_policy
from stratum_open.profiles import apply
from stratum_open.review_large import LENSES, review_files

DIFF = """diff --git a/app/users.py b/app/users.py
--- a/app/users.py
+++ b/app/users.py
@@ -1,3 +1,5 @@ import sqlite3
 import sqlite3
+def count(rows):
+    return len(rows) / len([r for r in rows if r])
 x = 1
diff --git a/app/auth/session.py b/app/auth/session.py
--- a/app/auth/session.py
+++ b/app/auth/session.py
@@ -1,2 +1,4 @@
 import jwt
+def check(token):
+    return jwt.decode(token, options={"verify_signature": False})
diff --git a/package-lock.json b/package-lock.json
--- a/package-lock.json
+++ b/package-lock.json
@@ -1,1 +1,2 @@
 {
+  "lockfileVersion": 3,
diff --git a/README.md b/README.md
--- a/README.md
+++ b/README.md
@@ -1,1 +1,2 @@
 # Widget
+Now with sessions.
"""


def policy(extra=""):
    return parse_policy("review:\n  profile: large\n  parallel_calls: 1\n" + extra)


def file_answer(findings=(), risk=40, summary="Changes the file."):
    return json.dumps({"risk_score": risk, "change_summary": summary, "why_it_matters": "It matters.", "findings": list(findings)})


def finding(path, code, title="Signature not verified", severity="high", line=3, category="security"):
    return {"file": path, "code": code, "line": line, "severity": severity, "category": category, "title": title,
            "detail": "Any token is accepted, so anyone can pass as any user.", "fix": "Verify the signature."}


def run(model, extra="", **kwargs):
    """The per-file passes alone, with the large profile's defaults under `extra`."""
    return review_files(title="Sessions", description="Adds token checks.", files=parse_diff(DIFF),
                        policy=apply(policy(extra), "large"), model=model, **kwargs)


def test_areas_come_from_the_path():
    assert areas.areas("app/auth/login.py") == ["auth"]
    assert areas.areas("src/decision.py") == []  # "ci" only as its own name
    assert areas.areas(".github/workflows/ci.yml") == ["workflow"]
    assert areas.areas("latest.py") == [] and areas.areas("attestation.py") == []
    assert "tests" in areas.areas("tests/test_users.py") and "tests" in areas.areas("web/app.spec.ts")
    assert areas.generated("package-lock.json") and areas.generated("web/dist/app.js")
    assert areas.generated("rebuild/tool.py") == ""


def test_files_are_ranked_and_capped():
    model = FakeModel(file_answer(), file_answer())
    result = run(model, "  file_reviews: 2\n  lens_files: 0\n")
    assert len(model.prompts) == 2
    by_path = {f.path: f for f in result.files}
    assert [f.path for f in result.files if f.rank][:2] == ["app/auth/session.py", "app/users.py"]  # the auth file first
    assert by_path["app/auth/session.py"].status == by_path["app/users.py"].status == "reviewed"
    assert by_path["README.md"].reason == "beyond the number of files reviewed one at a time"
    assert by_path["package-lock.json"].status == "skipped" and not by_path["package-lock.json"].rank
    assert "File to review: app/auth/session.py. Touches authentication" in model.prompts[0][1]


def test_lens_files_get_one_call_per_lens_of_the_mode():
    model = FakeModel()
    result = run(model, "  file_reviews: 2\n  lens_files: 1\n")
    assert len(model.prompts) == 3 + 1  # security, performance and logic on the top file; one pass on the next
    systems = [system for system, _ in model.prompts]
    assert all(LENSES[lens] in systems[i] for i, lens in enumerate(("security", "performance", "logic")))
    assert result.files[0].lenses == ("security", "performance", "logic") and result.files[1].lenses == ()

    model = FakeModel()
    run(model, "  mode: security\n  file_reviews: 1\n  lens_files: 1\n")
    assert len(model.prompts) == 2  # the mode's own lens and logic


def test_findings_are_placed_by_the_quoted_code_and_filtered():
    code = '    return jwt.decode(token, options={"verify_signature": False})'
    model = FakeModel(
        file_answer([finding("app/auth/session.py", code, severity="medium"), finding("app/users.py", "x = 1", title="Elsewhere")]),
        file_answer([finding("app/auth/session.py", code, severity="critical"),
                     finding("app/auth/session.py", "def check(token):", title="New function check"),
                     finding("app/auth/session.py", "def check(token):", title="Naming", severity="low")]),
        file_answer(),
    )
    result = run(model, "  file_reviews: 1\n  lens_files: 1\n  mode: performance\n")
    [kept] = result.findings
    assert (kept.path, kept.line_start, kept.severity) == ("app/auth/session.py", 3, "critical")  # one per title, the worst copy
    assert result.dropped == 3  # another file's, a narration and a low one


def test_a_failed_call_and_a_model_that_raises():
    class Raising:
        name = "boom"

        def complete(self, system, user, **kwargs):
            raise RuntimeError("token sk-secret-value in the message")

    result = run(Raising(), "  file_reviews: 1\n  lens_files: 0\n")
    assert result.files[0].status == "failed" and not result.complete
    assert result.calls[0]["error"] == "the model call failed (RuntimeError)"  # never the message, which may carry secrets

    result = run(FakeModel(Reply("", "m", 0, error="timed out")), "  file_reviews: 1\n  lens_files: 0\n")
    assert result.failed_calls == 1 and result.files[0].reason == "the model didn't answer for this file"


def test_parallel_calls_keep_each_answer_with_its_file():
    class ByFile:
        name = "by-file"

        def __init__(self):
            self.active = self.most = 0
            self.lock = threading.Lock()

        def complete(self, system, user, **kwargs):
            with self.lock:
                self.active += 1
                self.most = max(self.most, self.active)
            path = re.search(r"File to review: (\S+)\.", user).group(1)
            time.sleep(0.05 if "auth" in path else 0.01)
            with self.lock:
                self.active -= 1
            return Reply(file_answer(summary=f"About {path}."), self.name, 0)

    model = ByFile()
    result = run(model, "  file_reviews: 3\n  lens_files: 1\n  parallel_calls: 4\n")
    assert model.most > 1
    assert {f.path: f.change_summary for f in result.files if f.status == "reviewed"} == {
        "app/auth/session.py": "About app/auth/session.py.", "app/users.py": "About app/users.py.", "README.md": "About README.md."}


def test_only_the_files_changed_since_the_last_review():
    model = FakeModel()
    result = run(model, "  lens_files: 0\n", only_paths={"app/users.py"})
    assert len(model.prompts) == 1
    assert {f.path: f.reason for f in result.files}["app/auth/session.py"] == "not changed since the last review"


def test_the_engine_runs_the_passes_then_the_walkthrough():
    code = '    return jwt.decode(token, options={"verify_signature": False})'
    synthesis = json.dumps({"summary": "Adds unverified tokens.", "top_priorities": ["The signature check"],
                            "executive_summary": "Risky.", "walkthrough": "- session.py: adds check()."})
    model = FakeModel(
        file_answer([finding("app/auth/session.py", code)]), file_answer(), file_answer(),  # per file
        json.dumps({"summary": "Adds checks.", "matches_description": "yes", "findings": []}),  # the chunked review
        synthesis,
    )
    result = analyze(title="Sessions", description="Adds token checks.", files=parse_diff(DIFF),
                     policy=parse_policy("review:\n  profile: large\n  parallel_calls: 1\n  lens_files: 0\n"), model=model)
    assert result.profile == "large" and len(model.prompts) == 5
    assert "Signature not verified" in {f.title for f in result.findings}
    assert result.large.synthesized and result.large.walkthrough == "- session.py: adds check()."
    assert result.large.top_priorities == ["The signature check"] and result.complete
    assert "Signature not verified" in model.prompts[-1][1]  # the walkthrough is written from the settled findings


def test_the_small_profile_makes_no_extra_calls():
    model = FakeModel(json.dumps({"summary": "", "findings": []}))
    result = analyze(title="t", description="", files=parse_diff(DIFF), policy=parse_policy("review:\n  profile: small\n"), model=model)
    assert len(model.prompts) == 1 and result.large is None


def test_a_walkthrough_that_doesnt_answer_falls_back():
    model = FakeModel(file_answer(summary="Adds check()."), file_answer(), file_answer(),
                      json.dumps({"summary": "", "findings": []}), "not json at all")
    result = analyze(title="Sessions", description="", files=parse_diff(DIFF),
                     policy=parse_policy("review:\n  profile: large\n  parallel_calls: 1\n  lens_files: 0\n"), model=model)
    assert not result.large.synthesized and result.complete  # a missing walkthrough leaves no code unreviewed
    assert result.large.walkthrough.startswith("This PR touches:\n- app/auth/session.py: Adds check().")


def test_the_summary_shows_priorities_and_a_walkthrough():
    summary = publish.Summary(findings=[], inline=set(), policy=parse_policy(None), model_name="big",
                              priorities=["The signature check"], walkthrough="- session.py: adds check().")
    body = publish.render_summary(summary, publish.State())
    assert "**Check first:**\n- The signature check" in body
    assert "<details><summary>Walkthrough</summary>\n\n- session.py: adds check()." in body


def test_a_failed_walkthrough_still_publishes_its_fallbacks():
    from fakes import FakeGitHub

    from stratum_open.runner import Context, review_pull

    code = '    return jwt.decode(token, options={"verify_signature": False})'
    gh = FakeGitHub(diff=DIFF)
    model = FakeModel(file_answer([finding("app/auth/session.py", code)], summary="Adds check()."), file_answer(), file_answer(),
                      json.dumps({"summary": "Adds token checks.", "matches_description": "yes", "findings": []}), "not json")
    review_pull(Context(gh=gh, policy=parse_policy("review:\n  profile: large\n  parallel_calls: 1\n  lens_files: 0\n"),
                        model=model, bot="stratum[bot]", log=lambda line: None), 7)
    body = gh.issue_comments_[7][0]["body"]
    assert "**What this PR does:** Adds token checks." in body  # the chunked review's, not the fallback's generic sentence
    assert "**Check first:**" in body and "\n- Signature not verified in app/auth/session.py" in body
    assert "<details><summary>Walkthrough</summary>\n\nThis PR touches:\n- app/auth/session.py: Adds check()." in body
