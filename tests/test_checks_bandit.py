import pytest

from stratum_open.checks import bandit, run_checks
from stratum_open.diff import parse_diff
from stratum_open.policy import Policy


def diff(path, added=(), context=(), *, start=1):
    head = [f"diff --git a/{path} b/{path}", f"--- a/{path}", f"+++ b/{path}",
            f"@@ -{start},{len(context)} +{start},{len(context) + len(added)} @@"]
    return "\n".join(head + [f" {line}" for line in context] + [f"+{line}" for line in added]) + "\n"


def issue(line, severity="HIGH", confidence="HIGH", test_id="B602", path="app.py", lines=None):
    return {
        "path": path, "test_id": test_id, "test_name": "subprocess_popen_with_shell_equals_true",
        "severity": severity, "confidence": confidence, "text": "subprocess call with shell=True identified.",
        "line": line, "lines": lines or [line],
    }


def test_no_contents_means_no_bandit():
    assert bandit.check(parse_diff(diff("app.py", ["import os"]))) == []


def test_without_bandit_installed_it_is_skipped_quietly(monkeypatch):
    monkeypatch.setattr(bandit, "available", lambda: False)
    files = parse_diff(diff("app.py", ["import os"]))
    assert bandit.check(files, {"app.py": "import os\n"}) == []


def test_only_issues_on_added_lines_bandit_is_sure_of_are_kept(monkeypatch):
    monkeypatch.setattr(bandit, "available", lambda: True)
    monkeypatch.setattr(bandit, "run_bandit", lambda sources: [
        issue(2),  # added, high/high: kept as high
        issue(3, severity="HIGH", confidence="MEDIUM"),  # added: kept as medium
        issue(4, severity="MEDIUM", confidence="MEDIUM", test_id="B608"),  # added: kept as low
        issue(2, confidence="LOW"),  # low confidence: dropped
        issue(3, severity="LOW", test_id="B404"),  # low severity: dropped
        issue(1),  # an unchanged line: dropped
        issue(1, lines=[1, 2, 3]),  # starts on an unchanged line, spans added ones: kept on the first added
    ])
    files = parse_diff(diff("app.py", ["b()", "c()", "d()"], context=["a()"]))
    findings = bandit.check(files, {"app.py": "a()\nb()\nc()\nd()\n"})
    assert [(f.rule, f.severity, f.line_start) for f in findings] == [
        ("bandit/B602", "high", 2), ("bandit/B602", "medium", 3), ("bandit/B608", "low", 4), ("bandit/B602", "high", 2),
    ]
    assert all(f.source == "check" and f.category == "security" for f in findings)


def test_test_files_deleted_files_and_other_languages_are_not_sent(monkeypatch):
    sent = []
    monkeypatch.setattr(bandit, "available", lambda: True)
    monkeypatch.setattr(bandit, "run_bandit", lambda sources: sent.append(sorted(sources)) or [])
    text = diff("tests/test_app.py", ["x = 1"]) + diff("app.js", ["x = 1"]) + diff("src/app.py", ["x = 1"])
    contents = {"tests/test_app.py": "x = 1\n", "app.js": "x = 1\n", "src/app.py": "x = 1\n"}
    bandit.check(parse_diff(text), contents)
    assert sent == [["src/app.py"]]


def test_at_most_twenty_files_go_to_bandit(monkeypatch):
    sent = []
    monkeypatch.setattr(bandit, "available", lambda: True)
    monkeypatch.setattr(bandit, "run_bandit", lambda sources: sent.append(len(sources)) or [])
    text = "".join(diff(f"pkg/m{i}.py", ["x = 1"]) for i in range(25))
    bandit.check(parse_diff(text), {f"pkg/m{i}.py": "x = 1\n" for i in range(25)})
    assert sent == [20]


def test_a_bandit_crash_is_reported_not_raised(monkeypatch):
    monkeypatch.setattr(bandit, "available", lambda: True)

    def crash(sources):
        raise RuntimeError("bandit broke")

    monkeypatch.setattr(bandit, "run_bandit", crash)
    findings = run_checks(parse_diff(diff("app.py", ["x = 1"])), Policy(), contents={"app.py": "x = 1\n"})
    (error,) = [f for f in findings if f.rule == "checks/error"]
    assert "Bandit" in error.title and "bandit broke" in error.detail


def test_real_bandit_finds_issues_on_added_lines_only():
    pytest.importorskip("bandit")
    source = "import subprocess\nimport hashlib\n\n\ndef run(cmd):\n    subprocess.call(cmd, shell=True)\n    return hashlib.md5(cmd.encode()).hexdigest()\n"
    text = diff("tools/run.py", ["    subprocess.call(cmd, shell=True)"], context=["import subprocess", "import hashlib", "", "", "def run(cmd):"])
    findings = bandit.check(parse_diff(text), {"tools/run.py": source})
    assert [(f.rule, f.severity, f.line_start) for f in findings] == [("bandit/B602", "high", 6)]


def test_a_finding_on_a_line_the_pr_marks_nosec_says_so(monkeypatch):
    monkeypatch.setattr(bandit, "available", lambda: True)
    monkeypatch.setattr(bandit, "run_bandit", lambda sources: [issue(1), issue(2), issue(3)])
    files = parse_diff(diff("app.py", ["a()  # nosec", "b()  #nosec B602", "c()"]))
    notes = ["`# nosec`" in f.detail for f in bandit.check(files, {"app.py": "a()  # nosec\nb()  #nosec B602\nc()\n"})]
    assert notes == [True, True, False]


def test_real_bandit_ignores_nosec_the_pr_adds():
    pytest.importorskip("bandit")
    context = ["import hashlib, pickle, tempfile", "", "", "def load(blob):"]
    added = [
        "    data = pickle.loads(blob)  # nosec",
        "    path = tempfile.mktemp()  # nosec B306",
        "    return hashlib.md5(blob).hexdigest()  # nosec: B324",
    ]
    source = "\n".join(context + added) + "\n"
    findings = bandit.check(parse_diff(diff("app/load.py", added, context=context)), {"app/load.py": source})
    assert [(f.rule, f.line_start) for f in findings] == [("bandit/B301", 5), ("bandit/B306", 6), ("bandit/B324", 7)]
    assert all("`# nosec`" in f.detail for f in findings)


def test_a_file_moved_out_of_the_tests_goes_to_bandit_with_every_line(monkeypatch):
    sent = []
    monkeypatch.setattr(bandit, "available", lambda: True)
    monkeypatch.setattr(bandit, "run_bandit", lambda sources: sent.append(sorted(sources)) or [issue(1, path="app/helper.py")])
    text = "diff --git a/tests/helper.py b/app/helper.py\nsimilarity index 100%\nrename from tests/helper.py\nrename to app/helper.py\n"
    findings = bandit.check(parse_diff(text), {"app/helper.py": "subprocess.run(cmd, shell=True)\n"})
    assert sent == [["app/helper.py"]]
    assert [(f.rule, f.line_start) for f in findings] == [("bandit/B602", 1)]


def test_a_file_moved_between_folders_of_code_isnt_sent_again(monkeypatch):
    sent = []
    monkeypatch.setattr(bandit, "available", lambda: True)
    monkeypatch.setattr(bandit, "run_bandit", lambda sources: sent.append(sorted(sources)) or [])
    text = "diff --git a/lib/helper.py b/app/helper.py\nsimilarity index 100%\nrename from lib/helper.py\nrename to app/helper.py\n"
    assert bandit.check(parse_diff(text), {"app/helper.py": "subprocess.run(cmd, shell=True)\n"}) == []
    assert sent == []


def moved(old, new):
    return f"diff --git a/{old} b/{new}\nsimilarity index 100%\nrename from {old}\nrename to {new}\n"


def test_moves_cant_push_the_prs_own_code_out_of_bandit(monkeypatch):
    sent = []
    monkeypatch.setattr(bandit, "available", lambda: True)
    monkeypatch.setattr(bandit, "run_bandit", lambda sources: sent.append(list(sources)) or [issue(1, path="app/load.py")])
    helper = "".join(f"x{i} = {i}\n" for i in range(80))
    text = "".join(moved(f"tests/h{i}.py", f"src/h{i}.py") for i in range(20)) + diff("app/load.py", ["pickle.loads(data)"])
    contents = {**{f"src/h{i}.py": helper for i in range(20)}, "app/load.py": "pickle.loads(data)\n"}
    findings = bandit.check(parse_diff(text), contents)
    assert sent[0][0] == "app/load.py" and len(sent[0]) == 20
    assert [(f.path, f.rule) for f in findings] == [("app/load.py", "bandit/B602")]
    read, left_out = bandit.plan(parse_diff(text), contents)
    assert read[0] == "app/load.py" and left_out == ["src/h19.py"]


def test_the_plan_names_python_files_bandit_has_no_text_for():
    text = diff("app/a.py", ["x = 1"]) + diff("app/b.py", ["y = 2"]) + diff("tests/test_a.py", ["z = 3"])
    assert bandit.plan(parse_diff(text), {"app/a.py": "x = 1\n"}) == (["app/a.py"], ["app/b.py"])
