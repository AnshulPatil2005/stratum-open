import json
import os
import subprocess
from pathlib import Path

import pytest
from fakes import FakeGitHub

from stratum_open.checks import actionlint, run_checks, shellcheck
from stratum_open.diff import parse_diff
from stratum_open.policy import Policy, parse_policy
from stratum_open.runner import Context, review_pull

ON = parse_policy("checks:\n  actionlint: true\n")
FLOW = ".github/workflows/ci.yml"
needs_actionlint = pytest.mark.skipif(not actionlint.available(), reason="actionlint isn't on PATH")
needs_both = pytest.mark.skipif(not (actionlint.available() and shellcheck.available()), reason="actionlint and ShellCheck aren't both on PATH")


def new_file(path, text):
    lines = text.rstrip("\n").split("\n")
    head = [f"diff --git a/{path} b/{path}", "new file mode 100644", "--- /dev/null", f"+++ b/{path}", f"@@ -0,0 +1,{len(lines)} @@"]
    return "\n".join(head + [f"+{line}" for line in lines]) + "\n"


def changed(path, before, after, at):
    """A diff whose only change is line `at` (1-based) of `after`, which replaces the same line of `before`."""
    old, new = before.rstrip("\n").split("\n"), after.rstrip("\n").split("\n")
    body = [f" {line}" for line in new[:at - 1]] + [f"-{old[at - 1]}", f"+{new[at - 1]}"] + [f" {line}" for line in new[at:]]
    head = [f"diff --git a/{path} b/{path}", f"--- a/{path}", f"+++ b/{path}", f"@@ -1,{len(old)} +1,{len(new)} @@"]
    return "\n".join(head + body) + "\n"


def issue(line, kind="job-needs", message='job "a" needs job "nope" which does not exist in this workflow', path=FLOW):
    return {"path": path, "kind": kind, "message": message, "line": line}


def fake_actionlint(monkeypatch, issues=(), sent=None):
    monkeypatch.setattr(actionlint, "available", lambda: True)
    monkeypatch.setattr(actionlint, "run_actionlint", lambda sources: (sent.append(dict(sources)) if sent is not None else None) or list(issues))


WORKFLOW = """on: push
jobs:
  a:
    runs-on: ubuntu-latest
    needs: [nope]
    steps:
      - run: |
          echo one
          x = 1
      - run: echo ${{ github.event.issue.title }}
      - run: >
          y = 2
"""


def test_no_contents_means_no_actionlint():
    assert actionlint.check(parse_diff(new_file(FLOW, WORKFLOW))) == []


def test_without_actionlint_on_path_it_is_skipped_quietly(monkeypatch):
    monkeypatch.setattr(actionlint, "available", lambda: False)
    files = parse_diff(new_file(FLOW, WORKFLOW))
    assert actionlint.check(files, {FLOW: WORKFLOW}) == []
    assert not [f for f in run_checks(files, ON, contents={FLOW: WORKFLOW}) if f.rule.startswith("actionlint/") or f.rule == "checks/error"]


def test_it_runs_only_when_the_policy_turns_it_on(monkeypatch):
    sent = []
    fake_actionlint(monkeypatch, [issue(5)], sent)
    files = parse_diff(new_file(FLOW, WORKFLOW))
    assert not [f for f in run_checks(files, Policy(), contents={FLOW: WORKFLOW}) if f.rule.startswith("actionlint/")] and sent == []
    assert [f.rule for f in run_checks(files, ON, contents={FLOW: WORKFLOW}) if f.rule.startswith("actionlint/")] == ["actionlint/job-needs"]
    assert Policy().checks.actionlint is False


def test_only_issues_on_added_lines_of_kinds_kept_are_reported(monkeypatch):
    before = WORKFLOW.replace("needs: [nope]", "needs: []")
    fake_actionlint(monkeypatch, [
        issue(5),  # the changed line: kept, and high, since the job can't run
        issue(3),  # an unchanged line: dropped
        issue(5, kind="runner-label", message='label "gpu" is unknown'),  # a self-hosted runner's label: dropped
        issue(5, kind="expression", message='"github.event.issue.title" is potentially untrusted. avoid using it directly in inline scripts'),  # the workflow check's
        issue(5, kind="action", message='input "fetch-dept" is not defined in action "actions/checkout@v4"'),  # kept, medium
    ])
    files = parse_diff(changed(FLOW, before, WORKFLOW, 5))
    findings = actionlint.check(files, {FLOW: WORKFLOW})
    assert [(f.rule, f.severity, f.line_start) for f in findings] == [("actionlint/job-needs", "high", 5), ("actionlint/action", "medium", 5)]
    assert all(f.source == "check" and f.category == "bug" for f in findings)
    assert findings[0].title == 'actionlint (job-needs): `job "a" needs job "nope" which does not exist in this workflow`'


def test_shellcheck_in_a_run_script_goes_on_the_scripts_line(monkeypatch):
    sc = "shellcheck reported issue in this script: SC{}:{}:{}:3: {}"
    fake_actionlint(monkeypatch, [
        issue(7, kind="shellcheck", message=sc.format(2283, "error", 2, "Remove spaces around = to assign")),  # script line 2 of `run: |`: line 9
        issue(7, kind="shellcheck", message=sc.format(2086, "info", 1, "Double quote to prevent globbing")),  # not kept
        issue(10, kind="shellcheck", message=sc.format(WARNING, "warning", 1, "This assignment is only seen by the forked process")),  # inline: line 10
        issue(11, kind="shellcheck", message=sc.format(2283, "error", 1, "Remove spaces around = to assign")),  # folded: its first line, 12
        issue(11, kind="shellcheck", message=sc.format(2283, "error", 2, "Remove spaces around = to assign")),  # folded, later: not known
        issue(7, kind="shellcheck", message="shellcheck reported something else"),
    ])
    findings = actionlint.check(parse_diff(new_file(FLOW, WORKFLOW)), {FLOW: WORKFLOW})
    assert [(f.rule, f.line_start) for f in findings] == [("shellcheck/SC2283", 9), (f"shellcheck/SC{WARNING}", 10), ("shellcheck/SC2283", 12)]
    assert findings[0].title.startswith("ShellCheck SC2283 in a `run:` script")


WARNING = 2097


def test_the_scripts_line_follows_the_block_it_is_in():
    text = "steps:\n  - run: |-  # a comment\n      a\n      b\n  - run: echo\n  - name: x\n"
    assert [actionlint.script_line(text, 2, n) for n in (1, 2)] == [3, 4]
    assert actionlint.script_line(text, 5, 1) == 5 and actionlint.script_line(text, 5, 2) is None
    assert actionlint.script_line(text, 6, 1) is None and actionlint.script_line(text, 99, 1) is None


def test_text_from_the_pr_stays_inside_a_code_span(monkeypatch):
    fake_actionlint(monkeypatch, [issue(5, kind="syntax-check", message='unexpected key "`@mona` [x](http://x)" for "job" section')])
    [finding] = actionlint.check(parse_diff(new_file(FLOW, WORKFLOW)), {FLOW: WORKFLOW})
    assert finding.title.count("`") == 2 and finding.detail.count("`") == 2 and "'@mona'" in finding.detail


def test_only_workflows_are_sent_and_at_most_twenty(monkeypatch):
    sent = []
    fake_actionlint(monkeypatch, sent=sent)
    text = new_file("action.yml", "runs: {}") + new_file(".github/workflows/sub/x.yml", "on: push") + new_file("docs/ci.yml", "on: push")
    text += "".join(new_file(f".github/workflows/w{i}.yaml", "on: push") for i in range(22))
    contents = {f.path: "on: push\n" for f in parse_diff(text)}
    actionlint.check(parse_diff(text), contents)
    assert len(sent[0]) == 20 and all(path.startswith(".github/workflows/w") for path in sent[0])
    read, left_out = actionlint.plan(parse_diff(text), {k: v for k, v in contents.items() if k != ".github/workflows/w0.yaml"})
    assert len(read) == 20 and len(left_out) == 2  # one past the limit, one without its text


def test_a_file_moved_into_the_workflows_goes_to_actionlint_with_every_line(monkeypatch):
    fake_actionlint(monkeypatch, [issue(5)])
    text = f"diff --git a/docs/ci.yml b/{FLOW}\nsimilarity index 100%\nrename from docs/ci.yml\nrename to {FLOW}\n"
    assert [(f.rule, f.line_start) for f in actionlint.check(parse_diff(text), {FLOW: WORKFLOW})] == [("actionlint/job-needs", 5)]


def test_an_actionlint_crash_is_reported_not_raised(monkeypatch):
    monkeypatch.setattr(actionlint, "available", lambda: True)

    def crash(sources):
        raise RuntimeError("actionlint broke")

    monkeypatch.setattr(actionlint, "run_actionlint", crash)
    [error] = [f for f in run_checks(parse_diff(new_file(FLOW, WORKFLOW)), ON, contents={FLOW: WORKFLOW}) if f.rule == "checks/error"]
    assert "actionlint" in error.title and "actionlint broke" in error.detail and error.severity == "low"


def fake_programs(monkeypatch, tmp_path, *names):
    folder = tmp_path / "bin"
    folder.mkdir(exist_ok=True)
    paths = []
    for name in names:
        program = folder / (name + (".exe" if os.name == "nt" else ""))
        program.write_text("")
        program.chmod(0o755)
        paths.append(str(program))
    monkeypatch.setenv("PATH", str(folder))
    return paths


def test_actionlint_is_started_where_the_pr_cant_steer_it(monkeypatch, tmp_path):
    calls = []
    program, checker = fake_programs(monkeypatch, tmp_path, "actionlint", "shellcheck")

    def run(command, **kwargs):
        folder = Path(kwargs["cwd"])
        files = sorted(p.relative_to(folder).as_posix() for p in folder.rglob("*") if p.is_file())
        calls.append((command, kwargs, files, (folder / "actionlint.yaml").read_text()))
        return subprocess.CompletedProcess(command, 0, stdout="[]", stderr="")

    monkeypatch.setattr(actionlint.subprocess, "run", run)
    monkeypatch.setenv("SHELLCHECK_OPTS", "--external-sources")
    checkout = tmp_path / "checkout"
    checkout.mkdir()
    monkeypatch.chdir(checkout)
    assert actionlint.run_actionlint({FLOW: "on: push\n", ".github/workflows/actionlint.yaml": "on: push\n"}) == []
    [(command, kwargs, written, config)] = calls
    # On Windows actionlint hangs handing ShellCheck a long script, so it gets none there.
    assert command[0] == program and "-pyflakes=" in command and config == ""
    assert f"-shellcheck={checker if os.name != 'nt' else ''}" in command
    assert command[command.index("-config-file") + 1] == str(Path(kwargs["cwd"]) / "actionlint.yaml")
    assert written == [".github/workflows/0.yml", ".github/workflows/1.yml", "actionlint.yaml"]
    assert [Path(arg).as_posix() for arg in command[-2:]] == written[:2]
    assert checkout not in Path(kwargs["cwd"]).parents and not Path(kwargs["cwd"]).exists()
    assert kwargs["timeout"] == actionlint.TIMEOUT_SECONDS and "SHELLCHECK_OPTS" not in kwargs["env"]
    calls.clear()
    actionlint.run_actionlint({FLOW: "on: push\n"}, scripts=False)
    assert "-shellcheck=" in calls[0][0]


def test_without_shellcheck_actionlint_checks_no_scripts(monkeypatch, tmp_path):
    calls = []
    fake_programs(monkeypatch, tmp_path, "actionlint")
    monkeypatch.setattr(actionlint.subprocess, "run", lambda command, **kwargs: calls.append(command) or subprocess.CompletedProcess(command, 0, stdout="[]", stderr=""))
    actionlint.run_actionlint({FLOW: "on: push\n"})
    assert "-shellcheck=" in calls[0]


def test_an_actionlint_that_fails_or_cant_be_read_is_an_error_not_a_clean_result(monkeypatch, tmp_path):
    fake_programs(monkeypatch, tmp_path, "actionlint")
    for code, stdout in ((2, ""), (0, "not json"), (1, "{}"), (3, "[]")):
        monkeypatch.setattr(actionlint.subprocess, "run", lambda command, code=code, stdout=stdout, **kwargs: subprocess.CompletedProcess(command, code, stdout=stdout, stderr="boom"))
        with pytest.raises(actionlint.ActionlintError, match=f"exit code {code}"):
            actionlint.run_actionlint({FLOW: "on: push\n"})
    [error] = [f for f in run_checks(parse_diff(new_file(FLOW, WORKFLOW)), ON, contents={FLOW: WORKFLOW}) if f.rule == "checks/error"]
    assert "actionlint check didn't run" in error.title and "boom" not in error.detail


def test_actionlint_running_out_of_time_is_reported(monkeypatch, tmp_path):
    fake_programs(monkeypatch, tmp_path, "actionlint")

    def too_slow(command, **kwargs):
        raise subprocess.TimeoutExpired(command, kwargs["timeout"])

    monkeypatch.setattr(actionlint.subprocess, "run", too_slow)
    [error] = [f for f in run_checks(parse_diff(new_file(FLOW, WORKFLOW)), ON, contents={FLOW: WORKFLOW}) if f.rule == "checks/error"]
    assert "actionlint took longer than 15 s" in error.detail


def test_reports_from_actionlint_that_make_no_sense_are_left_out(monkeypatch, tmp_path):
    fake_programs(monkeypatch, tmp_path, "actionlint")
    report = [
        {"message": "m", "filepath": ".github/workflows/0.yml", "line": 3, "kind": "syntax-check"},
        {"message": "m", "filepath": ".github/workflows/9.yml", "line": 3, "kind": "syntax-check"},
        {"message": "m", "filepath": ".github/workflows/0.yml", "line": "3", "kind": "syntax-check"},
        {"message": "m", "filepath": ".github/workflows/0.yml", "line": 3},
        "not an issue",
    ]
    monkeypatch.setattr(actionlint.subprocess, "run", lambda command, **kwargs: subprocess.CompletedProcess(command, 1, stdout=json.dumps(report), stderr=""))
    assert actionlint.run_actionlint({FLOW: "on: push\n"}) == [{"path": FLOW, "kind": "syntax-check", "message": "m", "line": 3}]


def test_with_the_key_on_the_summary_names_workflows_actionlint_didnt_read(monkeypatch):
    monkeypatch.setattr("stratum_open.checks.bandit.available", lambda: False)
    sent = []
    fake_actionlint(monkeypatch, [issue(5)], sent)
    monkeypatch.setattr(actionlint, "MAX_FILE_CHARS", 300)
    big = ".github/workflows/big.yml"
    gh = FakeGitHub(diff=new_file(FLOW, WORKFLOW) + new_file(big, "on: push"), files={(FLOW, "a" * 40): WORKFLOW, (big, "a" * 40): "on: push\n" + "#\n" * 200})
    outcome = review_pull(Context(gh=gh, policy=ON, model=None, bot="stratum[bot]", log=lambda line: None), 7)
    assert "actionlint/job-needs" in {f.rule for f in outcome.findings} and [sorted(s) for s in sent] == [[FLOW]]
    assert f"Not read by actionlint (1 file past its limit of 20, too long, or without their text): `{big}`" in gh.issue_comments_[7][-1]["body"]


# ── the real actionlint ──────────────────────────────────────────────────

# Each a workflow with one mistake on line 5 that GitHub would trip on, and the rule it must get.
PLANTED = {
    "job-needs": "on: push\njobs:\n  a:\n    runs-on: ubuntu-latest\n    needs: [build]\n    steps:\n      - run: echo\n",
    "syntax-check": "on: push\njobs:\n  a:\n    runs-on: ubuntu-latest\n    step:\n      - run: echo\n",
    "action": "on: push\njobs:\n  a:\n    runs-on: ubuntu-latest\n    steps: [{uses: actions/checkout@v4, with: {fetch-dept: 0}}]\n",
}
# A workflow that works: a self-hosted runner, local actions and reusable workflows that
# aren't there, a PR's title passed safely, and `${{ }}` the workflow check already reports.
WORKING = """on:
  pull_request:
  workflow_dispatch:
    inputs:
      level: {type: choice, options: [a, b], default: a}
permissions:
  contents: read
jobs:
  build:
    runs-on: [self-hosted, linux, gpu-big]
    strategy:
      matrix:
        os: [ubuntu-latest]
    steps:
      - uses: actions/checkout@v4
        with:
          fetch-depth: 0
      - uses: ./.github/actions/setup
      - id: version
        run: echo "value=$(date +%s)" >> "$GITHUB_OUTPUT"
      - env:
          TITLE: ${{ github.event.pull_request.title }}
        run: |
          echo "$TITLE ${{ matrix.os }} ${{ steps.version.outputs.value }} ${{ inputs.level }}"
          cd "$RUNNER_TEMP" || exit 1
      - run: echo "${{ github.event.pull_request.title }}"
  call:
    needs: build
    uses: ./.github/workflows/reusable.yml
    secrets: inherit
"""


def run_real(text):
    return actionlint.check(parse_diff(new_file(FLOW, text)), {FLOW: text})


@needs_actionlint
def test_real_actionlint_catches_every_planted_case():
    for kind, text in PLANTED.items():
        assert f"actionlint/{kind}" in {f.rule for f in run_real(text)}, kind


@needs_actionlint
def test_real_actionlint_stays_quiet_on_a_workflow_that_works():
    assert run_real(WORKING) == []


@needs_actionlint
def test_real_actionlint_finds_issues_on_added_lines_only():
    broken = PLANTED["job-needs"]
    files = parse_diff(changed(FLOW, broken.replace("echo", "echo old"), broken, 7))
    assert actionlint.check(files, {FLOW: broken}) == []


@needs_actionlint
def test_real_actionlint_reads_no_config_from_where_the_review_runs(monkeypatch, tmp_path):
    (tmp_path / ".github").mkdir()
    (tmp_path / ".github" / "actionlint.yaml").write_text("paths:\n  .github/workflows/**/*.yml:\n    ignore: ['.*']\n")
    monkeypatch.chdir(tmp_path)
    assert "actionlint/job-needs" in {f.rule for f in run_real(PLANTED["job-needs"])}


@needs_both
def test_real_actionlint_runs_shellcheck_on_scripts_and_keeps_only_what_it_keeps():
    text = "on: push\njobs:\n  a:\n    runs-on: ubuntu-latest\n    steps:\n      - run: |\n          echo $UNQUOTED\n          x = 1\n"
    assert [(f.rule, f.line_start) for f in run_real(text)] == [("shellcheck/SC2283", 8)]
