import json

from fakes import FakeGitHub, FakeModel
from test_model import FakeSession

from stratum_open.diff import parse_diff, render, with_context
from stratum_open.model import Model
from stratum_open.policy import parse_policy
from stratum_open.profiles import apply
from stratum_open.review import review
from stratum_open.runner import Context, review_pull

SOURCE = [f"line {n}" for n in range(1, 21)]
NEW = SOURCE[:5] + ["added A"] + SOURCE[5:10] + ["added B"] + SOURCE[10:]  # new lines 6 and 12
TEXT = "\n".join(NEW) + "\n"
DIFF = """diff --git a/x.py b/x.py
--- a/x.py
+++ b/x.py
@@ -5,2 +5,3 @@
 line 5
+added A
 line 6
@@ -10,2 +11,3 @@
 line 10
+added B
 line 11
"""


def numbers(hunk):
    return [(line.kind, line.old, line.new) for line in hunk.lines]


def test_the_newline_after_a_diff_is_not_a_line():
    [file] = parse_diff(DIFF)
    assert numbers(file.hunks[-1]) == [(" ", 10, 11), ("+", None, 12), (" ", 11, 13)]  # no phantom blank line 14


def test_lines_around_each_hunk_come_from_the_file_once_each():
    [file] = parse_diff(DIFF)
    first, second = with_context(file, TEXT, 3).hunks
    assert [new for _, _, new in numbers(first)] == [2, 3, 4, 5, 6, 7, 8, 9, 10]  # the gap up to the next hunk, once
    assert [new for _, _, new in numbers(second)] == [11, 12, 13, 14, 15, 16]
    assert numbers(second)[-1] == (" ", 14, 16)  # old-side numbers shift past the added lines
    assert numbers(first)[0] == (" ", 2, 2) and (first.old_start, first.new_start) == (2, 2)
    assert "     3    line 3" in render(with_context(file, TEXT, 3))
    assert with_context(file, "\n".join(SOURCE), 3) is file  # stale text: the diff's own lines don't match
    assert with_context(file, TEXT, 0) is file and with_context(file, None, 3) is file


def test_the_start_and_end_of_the_file_and_a_removal():
    [file] = parse_diff("diff --git a/y.py b/y.py\n--- a/y.py\n+++ b/y.py\n@@ -1,1 +1,2 @@\n+first\n a\n@@ -3,2 +4,1 @@\n c\n-d\n")
    hunks = with_context(file, "first\na\nb\nc\n", 5).hunks
    assert [new for _, _, new in numbers(hunks[0])] == [1, 2, 3]  # nothing before line 1; up to the next hunk
    assert numbers(hunks[1]) == numbers(file.hunks[1])  # removed lines only: left as they were


def test_a_finding_on_a_context_line_is_about_the_whole_file():
    [file] = parse_diff(DIFF)
    model = FakeModel(json.dumps({"summary": "", "findings": [
        {"file": "x.py", "code": "line 3", "line": 3, "severity": "high", "title": "Wrong value", "detail": "Line 3 is wrong.", "fix": "Fix it."},
        {"file": "x.py", "code": "added A", "line": 6, "severity": "high", "title": "Bad addition", "detail": "A is wrong.", "fix": "Fix it."},
    ]}))
    policy = apply(parse_policy("review:\n  context_lines: 3\n"), "large")
    result = review(title="t", description="", files=[file], policy=policy, model=model, contents={"x.py": TEXT})
    assert "     3    line 3" in model.prompts[0][1]  # the model reads around the hunk
    assert {f.title: f.line_start for f in result.findings} == {"Wrong value": None, "Bad addition": 6}  # GitHub can't take line 3


def test_the_runner_fetches_the_text_when_the_profile_shows_context():
    # A .ts file: the checks alone would fetch a Python one, for Bandit.
    gh = FakeGitHub(diff=DIFF.replace("x.py", "x.ts"), files={("x.ts", "a" * 40): TEXT})
    model = FakeModel(json.dumps({"risk_score": 10, "change_summary": "Adds A and B.", "findings": []}),
                      json.dumps({"summary": "Adds two lines.", "matches_description": "yes", "findings": []}), "{}")
    policy = parse_policy("review:\n  profile: large\n  parallel_calls: 1\n  lens_files: 0\n")
    review_pull(Context(gh=gh, policy=policy, model=model, bot="stratum[bot]", log=lambda line: None), 7)
    assert "     3    line 3" in model.prompts[0][1] and "     3    line 3" in model.prompts[1][1]


def test_describe_asks_the_server_once():
    class Counting(FakeSession):
        calls = 0

        def post(self, url, **kwargs):
            Counting.calls += 1
            return super().post(url, **kwargs)

    session = Counting({("GET", "http://localhost:11434/api/version"): {"version": "1"},
                        ("POST", "http://localhost:11434/api/show"): {"details": {"parameter_size": "7B"}}})
    model = Model("http://localhost:11434/v1", "m", session=session)
    assert model.describe() == model.describe() and Counting.calls == 1
