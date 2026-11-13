"""PR-Agent on the planted-bug benchmark (benchmark/others), end to end and
without a model.

Three real cases go through the benchmark's runner and PR-Agent's own command
line, against a fake model server that reports the planted line whenever a
prompt shows it. This proves the plumbing: the case reaches PR-Agent as the
PR it is, and what PR-Agent answers comes back as rows `planted.py score`
takes.

Skipped unless PR_AGENT_PYTHON names a Python that has PR-Agent installed
(`pip install pr-agent==0.50.0` in an environment of its own). Unlike the
rest of the suite it needs the network: git fetches the three PRs, and their
data comes from benchmark/.cache, or from GitHub with GITHUB_TOKEN.

    PR_AGENT_PYTHON=~/pr-agent/bin/python python -m pytest tests/e2e/test_others_pr_agent.py

PR_AGENT_TRACE=<folder> keeps the prompts PR-Agent sent in that folder.
"""

from __future__ import annotations

import csv
import json
import os
import subprocess
import sys
from pathlib import Path

import pytest

from fake_model import FakeModelServer, Response

ROOT = Path(__file__).resolve().parents[2]
sys.path.insert(0, str(ROOT / "benchmark"))

from bench import _fetch  # noqa: E402
from planted import FINDING_COLUMNS, REVIEW_COLUMNS  # noqa: E402

PYTHON = os.environ.get("PR_AGENT_PYTHON")
pytestmark = [pytest.mark.slow, pytest.mark.skipif(not PYTHON, reason="set PR_AGENT_PYTHON to a Python that has PR-Agent installed")]

# A small diff, one too big for a single call, and a project with an AGENTS.md, each with the commands
# whose finding on the planted line comes back. For the big diff that is `review` alone, in two calls:
# the second pass of `improve`, which gives a suggestion its line numbers, doesn't fit an 8,192-token
# window there, and PR-Agent drops a suggestion that has none.
CASES = {"pallets/flask#5899": ("review", "improve"), "pallets/werkzeug#3337": ("review",), "scrapy/scrapy#8461": ("review", "improve")}

REVIEW = """review:
  estimated_effort_to_review_[1-5]: 2
  relevant_tests: |
    Yes
  key_issues_to_review:{issues}
  security_concerns: |
    No
"""
ISSUE = """
    - relevant_file: |
        {path}
      issue_header: |
        {title}
      issue_content: |
        {detail}
      start_line: {line}
      end_line: {line}"""
SUGGESTIONS = """code_suggestions:
- relevant_file: |
    {path}
  language: |
    python
  existing_code: |
    {code}
  suggestion_content: |
    This condition is the reverse of what the description says.
  improved_code: |
    pass
  one_sentence_summary: |
    Reversed condition
  label: |
    critical bug
"""
SCORES = """code_suggestions:
- suggestion_summary: |
    Reversed condition
  relevant_file: "{path}"
  relevant_lines_start: {line}
  relevant_lines_end: {line}
  suggestion_score: {score}
  why: |
    It decides which branch runs.
"""


class Reviewer(FakeModelServer):
    """A model that reports the planted line when a prompt shows it and
    nothing otherwise, in the three shapes PR-Agent asks for: a review, code
    suggestions, and the scores and line numbers of those suggestions."""

    def __init__(self, bugs: list[dict]):
        super().__init__()
        self.bugs = bugs

    def _next(self) -> Response:
        prompt = (self.calls[-1].get("body") or {}).get("prompt") or ""
        lines = prompt.split("\n")
        numbered = next((b for b in self.bugs if f"{b['line']} +{b['replace']}" in lines), None)
        if "AI-generated code suggestions" in prompt:  # the second pass: no line number to give means no finding
            bug = numbered or self.bugs[0]
            return Response.ok(SCORES.format(path=bug["path"], line=bug["line"], score=8 if numbered else 0))
        if "$PRCodeSuggestions" in prompt:  # a diff without line numbers
            bug = next((b for b in self.bugs if f"+{b['replace']}" in lines and f"## File: '{b['path']}'" in lines), None)
            return Response.ok(SUGGESTIONS.format(path=bug["path"], code=bug["replace"].strip()) if bug else "code_suggestions: []\n")
        issues = ""
        if numbered:
            issues = (ISSUE.format(path=numbered["path"], line=numbered["line"], title="Possible Bug", detail="This condition is the reverse of what the description says.")
                      + ISSUE.format(path=numbered["path"], line=1, title="Naming", detail="A remark far from the planted line."))
        return Response.ok(REVIEW.format(issues=issues or " []"))


def test_three_cases_reach_pr_agent_and_come_back_as_rows(tmp_path):
    every = json.loads((ROOT / "benchmark/planted/bugs.json").read_text(encoding="utf-8"))
    bugs = [b for b in every if f"{b['repo']}#{b['pr']}" in CASES]
    assert len(bugs) == len(CASES)
    (tmp_path / "bugs.json").write_text(json.dumps(bugs), encoding="utf-8")
    trace = Path(os.environ.get("PR_AGENT_TRACE") or tmp_path / "trace")
    run = tmp_path / "run"

    with Reviewer(bugs) as fake:
        for version in ("planted", "clean"):
            command = [sys.executable, str(ROOT / "benchmark/others/pr_agent.py"), "--bugs", str(tmp_path / "bugs.json"),
                       "--model", f"gpt-oss:20b@{fake.url}", "--pr-agent", PYTHON, "--unverified-window",
                       "--policy", str(ROOT / "benchmark/planted/variants/reasoning-low.yml"),
                       "--out", str(run / "raw" / f"pr-agent-{version}"), "--trace", str(trace), "--work", str(tmp_path / "work")]
            done = subprocess.run(command + ["--clean"] * (version == "clean"), capture_output=True, text=True, encoding="utf-8", errors="replace")
            assert done.returncode == 0, done.stdout + done.stderr
        asked = [call["body"] for call in fake.calls if call["path"] == "/api/generate"]

    record = json.loads((run / "raw/pr-agent-planted/pr_agent.json").read_text(encoding="utf-8"))
    variant = f"pr-agent-{record['versions']['pr-agent']}-reasoning-low"
    planted, clean = _rows(run / "raw/pr-agent-planted/reviews.csv"), _rows(run / "raw/pr-agent-clean/reviews.csv")
    findings = _rows(run / "raw/pr-agent-planted/findings.csv")
    assert list(planted[0]) == REVIEW_COLUMNS and list(findings[0]) == FINDING_COLUMNS

    for bug, row, control in zip(bugs, planted, clean):
        case = f"{bug['repo']}#{bug['pr']}"
        assert (row["case"], row["path"], int(row["line"]), row["kind"], row["set"]) == (case, bug["path"], bug["line"], bug["kind"], bug["set"])
        assert (row["model"], row["variant"], row["version"]) == ("gpt-oss:20b", variant, "planted")
        assert (row["shown"], row["error"], row["failed_calls"]) == ("yes", "", "0"), row
        assert row["calls"] == "3"  # a review, suggestions and their scores; or a review in two parts and suggestions
        # Its findings: the planted line from each command, and the remark far from it.
        mine = [f for f in findings if f["case"] == case]
        near = sorted((f["category"].split(":")[0], f["path"], int(f["line"])) for f in mine if f["near"] == "yes")
        assert near == sorted((command, bug["path"], bug["line"]) for command in CASES[case])
        assert [(f["category"], f["path"], f["line"], f["title"]) for f in mine if f["near"] != "yes"] == [("review", bug["path"], "1", "Naming")]
        assert (int(row["findings"]), int(row["near"])) == (len(mine), len(near))
        # As merged, PR-Agent is shown the line too and this model has nothing to say.
        assert (control["case"], control["version"], control["shown"], control["findings"], control["error"]) == (case, "clean", "yes", "0", "")

        # What PR-Agent was shown: the PR's own title and description, and the line as an added line of its file.
        slug = f"{bug['repo'].replace('/', '__')}__{bug['pr']}"
        pull, _ = _fetch(bug["repo"], int(bug["pr"]))
        with_bug = [path.read_text(encoding="utf-8") for path in sorted(trace.glob(f"{slug}-planted-review-*.txt"))]
        as_merged = [path.read_text(encoding="utf-8") for path in sorted(trace.glob(f"{slug}-clean-review-*.txt"))]
        assert with_bug and all(f"Title: '{pull['title'].strip()}'" in text for text in with_bug + as_merged)
        assert (pull["body"] or "").replace("\r\n", "\n").strip()[:60] in with_bug[0].replace("\r\n", "\n")
        assert any(f"{bug['line']} +{bug['replace']}" in text.split("\n") for text in with_bug)
        assert any(f"{bug['line']} +{bug['find']}" in text.split("\n") for text in as_merged)
        assert not any(f"{bug['line']} +{bug['replace']}" in text.split("\n") for text in as_merged)
        assert "AGENTS.md" not in "".join(with_bug)

    # PR-Agent's requests name no context window, which is why the server must be started with one,
    # and they carry the thinking level the policy asked for.
    assert asked and all(body["model"] == "gpt-oss:20b" and "num_ctx" not in body.get("options", {}) for body in asked)
    assert all(body.get("think") == "low" for body in asked)
    # Nothing was published, not even the files the local provider would write.
    assert not [path for path in (tmp_path / "work").glob("*/*.md")]

    # planted.py scores the folder, before and after the findings are marked.
    assert _score(run)[:6] == ["`gpt-oss:20b`", variant, "3", "3 of 3", "0 of 3", "3 of 3"]
    for row in findings:
        row["verdict"] = "right" if row["near"] == "yes" else "wrong"
    with (run / "raw/pr-agent-planted/findings.csv").open("w", newline="", encoding="utf-8") as handle:
        writer = csv.DictWriter(handle, fieldnames=FINDING_COLUMNS)
        writer.writeheader()
        writer.writerows(findings)
    assert _score(run)[6:10] == ["3 of 3 (3 + 0)", "3", "0 of 3 marked", "0"]


def _rows(path: Path) -> list[dict]:
    with path.open(encoding="utf-8", newline="") as handle:
        return list(csv.DictReader(handle))


def _score(run: Path) -> list[str]:
    """PR-Agent's row of the summary `planted.py score` writes for the run."""
    done = subprocess.run([sys.executable, str(ROOT / "benchmark/planted.py"), "score", str(run)], capture_output=True, text=True, encoding="utf-8", errors="replace")
    assert done.returncode == 0, done.stderr
    row = next(line for line in (run / "summary.md").read_text(encoding="utf-8").split("\n") if "| pr-agent-" in line)
    return [cell.strip() for cell in row.strip("|").split("|")]
