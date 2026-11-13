"""actionlint on the GitHub workflows a PR adds or changes, when
`checks.actionlint` is on and `actionlint` is on PATH (GitHub's runners don't
have it: the README says how to add it).

It finds what makes a workflow fail or do something else than it says: YAML
GitHub can't load, a key it doesn't know, a `needs:` naming no job, an
expression of the wrong type, an input a well-known action doesn't take.
Only issues on lines the PR adds are kept, from the workflow's full text in
`contents`.

Some of what it reports is left out:
- a runner label it doesn't know: a self-hosted runner's labels are listed in
  the project's own actionlint config, which isn't read;
- issue or PR text in a script (`${{ github.event.pull_request.title }}` in
  `run:`), which the workflow check (workflows.py) already reports;
- anything in DROPPED, which benchmark/results/2026-11-14-shell-checks says
  why.

With `shellcheck` on PATH, actionlint also runs it on each `run:` script, and
of that only what the ShellCheck check keeps (shellcheck.kept) is reported,
on the line of the script it's about (not on Windows, where that hangs on a
long script). Its Python check (pyflakes) is off.

No code from the PR runs, and the PR can't steer actionlint. It reads the
YAML and never runs a step. The files go in a temporary folder outside any
checkout, under names chosen here, with an empty config file of its own, so
the project's `actionlint.yaml` isn't read either: like the workflow check,
it judges the workflow as the PR's author wrote it. Local actions and
reusable workflows the PR names aren't there, and actionlint skips what it
would check about them. It runs as its own process with a time limit.
"""

from __future__ import annotations

import json
import os
import re
import subprocess
import tempfile
from typing import Any

from stratum_open.checks import shellcheck
from stratum_open.checks.common import checked_lines, find_program, moved_in
from stratum_open.diff import FileDiff
from stratum_open.findings import Finding

MAX_FILES = 20
MAX_FILE_CHARS = 1_000_000
TIMEOUT_SECONDS = 15
_WORKFLOW = re.compile(r"^\.github/workflows/[^/]+\.ya?ml$")  # GitHub runs only these
# actionlint's kinds of issue that are left out, and why.
DROPPED: dict[str, str] = {
    "runner-label": "a self-hosted runner's labels are in the project's config, which isn't read",
    "pyflakes": "the Python in a `run:` script isn't checked",
}
# Kinds of issue that stop the workflow, or the job, from running at all.
_FAILS = {"syntax-check", "job-needs", "workflow-call", "matrix", "id", "events"}
_UNTRUSTED = re.compile(r"is potentially untrusted")
_SHELLCHECK = re.compile(r"^shellcheck reported issue in this script: SC(\d+):(\w+):(\d+):\d+: (.*)$", re.DOTALL)
_RUN = re.compile(r"^\s*(?:-\s+)?run\s*:\s*(?P<rest>.*)$")


class ActionlintError(RuntimeError):
    pass


class ActionlintTimeout(ActionlintError):
    pass


def available() -> bool:
    return find_program("actionlint") is not None


def scope(path: str) -> frozenset[str]:
    """actionlint reads the workflows GitHub runs."""
    return frozenset({"actionlint"}) if _WORKFLOW.match(path) else frozenset()


def plan(files: list[FileDiff], contents: dict[str, str] | None) -> tuple[list[str], list[str]]:
    """The workflows actionlint reads, and the ones it would read but
    doesn't: past MAX_FILES, longer than MAX_FILE_CHARS, or without their
    text. Biggest change first."""
    contents = contents or {}
    candidates = [
        file for file in files
        if scope(file.path) and file.status != "deleted" and not file.binary and (file.added() or moved_in(file, scope))
    ]
    readable = [f for f in candidates if f.path in contents and len(contents[f.path]) <= MAX_FILE_CHARS]
    read = [file.path for file in sorted(readable, key=lambda f: -len(checked_lines(f, contents, scope)))[:MAX_FILES]]
    return read, [file.path for file in candidates if file.path not in read]


def check(files: list[FileDiff], contents: dict[str, str] | None = None) -> list[Finding]:
    if not contents or not available():
        return []
    targets, _ = plan(files, contents)
    if not targets:
        return []
    # A file moved here from elsewhere counts every line as added.
    added = {file.path: {number for number, _ in checked_lines(file, contents, scope)} for file in files if file.path in targets}
    findings = []
    for issue in run_actionlint({path: contents[path] for path in targets}):
        finding = _finding(issue, contents[issue["path"]], added.get(issue["path"], set()))
        if finding is not None:
            findings.append(finding)
    return findings


def _finding(issue: dict[str, Any], text: str, added: set[int]) -> Finding | None:
    kind, message = issue["kind"], issue["message"]
    if kind in DROPPED or _UNTRUSTED.search(message):
        return None
    if kind == "shellcheck":
        return _script_finding(issue, text, added)
    if issue["line"] not in added:
        return None
    return Finding(
        title=f"actionlint ({kind}): {_title(message)}",
        detail=f"actionlint reports {shellcheck.quote(message)}. "
               + ("GitHub won't run the workflow, or this job, as written." if kind in _FAILS else "The workflow doesn't do what this line says."),
        severity="high" if kind in _FAILS else "medium",
        category="bug",
        path=issue["path"],
        line_start=issue["line"],
        line_end=issue["line"],
        suggestion=f"actionlint's documentation of its `{kind}` check explains the rule: https://github.com/rhysd/actionlint/blob/main/docs/checks.md",
        source="check",
        rule=f"actionlint/{kind}",
        confidence=0.9,
    )


def _script_finding(issue: dict[str, Any], text: str, added: set[int]) -> Finding | None:
    """What ShellCheck found in a `run:` script, on the line it's about. actionlint
    puts it on the `run:` line, and says which line of the script it means."""
    found = _SHELLCHECK.match(issue["message"])
    if found is None:
        return None
    code, level, offset, message = int(found.group(1)), found.group(2), int(found.group(3)), found.group(4)
    why = shellcheck.kept(code, level)
    line = script_line(text, issue["line"], offset)
    if why is None or line is None or line not in added:
        return None
    return Finding(
        title=f"ShellCheck SC{code} in a `run:` script: {_title(message)}",
        detail=f"actionlint ran ShellCheck on this step's script, which reports SC{code}: {shellcheck.quote(message)}. {why}",
        severity="high" if level == "error" else "medium",
        category="bug",
        path=issue["path"],
        line_start=line,
        line_end=line,
        suggestion=f"See https://www.shellcheck.net/wiki/SC{code} for what it means and how to fix it.",
        source="check",
        rule=f"shellcheck/SC{code}",
        confidence=0.9,
    )


def script_line(text: str, run_line: int, offset: int) -> int | None:
    """The workflow's line for line `offset` of the script at `run_line`: the
    same line for `run: echo`, the lines below it for `run: |`. A folded
    `run: >` joins its lines, so only its first is known."""
    lines = text.split("\n")
    if not 0 < run_line <= len(lines):
        return None
    found = _RUN.match(lines[run_line - 1])
    if found is None:
        return None
    rest = re.sub(r"\s+#.*$", "", found.group("rest")).strip()
    if rest.startswith("|"):
        return run_line + offset
    if rest.startswith(">"):
        return run_line + 1 if offset == 1 else None
    return run_line if offset == 1 else None


def _title(message: str) -> str:
    first = message.replace("`", "'").replace("\n", " ").split(". ", 1)[0].rstrip(".")
    return "`" + (first[:77] + "..." if len(first) > 80 else first) + "`"


def run_actionlint(sources: dict[str, str], *, scripts: bool = True, timeout: float = TIMEOUT_SECONDS) -> list[dict[str, Any]]:
    """actionlint's issues for each workflow's text, as plain dicts keyed by
    our path. The files are written as `.github/workflows/<n>.yml` in a
    temporary folder, and actionlint runs once over all of them, with
    ShellCheck on the scripts when `scripts` and it's on PATH. Raises
    ActionlintError when it isn't there, doesn't finish, or can't be read."""
    program = find_program("actionlint")
    if program is None:
        raise ActionlintError("actionlint isn't on PATH any more.")
    # On Windows, actionlint 1.7.12 hangs handing ShellCheck a script of more than a few KB, so it gets none there.
    checker = find_program("shellcheck") if scripts and os.name != "nt" else None
    with tempfile.TemporaryDirectory(prefix="stratum-actionlint-", ignore_cleanup_errors=True) as folder:
        workflows = os.path.join(folder, ".github", "workflows")
        os.makedirs(workflows)
        paths: dict[str, str] = {}
        for index, (path, text) in enumerate(sources.items()):
            name = f"{index}.yml"
            # newline="" writes the line endings as they are, keeping line numbers.
            with open(os.path.join(workflows, name), "w", encoding="utf-8", errors="replace", newline="") as handle:
                handle.write(text)
            paths[name] = path
        config = os.path.join(folder, "actionlint.yaml")
        open(config, "w", encoding="utf-8").close()
        command = [
            program, "-no-color", "-format", "{{json .}}", "-config-file", config, "-pyflakes=", f"-shellcheck={checker or ''}",
            *[os.path.join(".github", "workflows", name) for name in paths],
        ]
        # SHELLCHECK_OPTS would add options of whoever set it to the scripts' run.
        env = {key: value for key, value in os.environ.items() if key.upper() != "SHELLCHECK_OPTS"}
        try:
            result = subprocess.run(command, capture_output=True, text=True, encoding="utf-8", errors="replace", timeout=timeout, cwd=folder, env=env)
        except subprocess.TimeoutExpired:
            raise ActionlintTimeout(f"actionlint took longer than {timeout:.0f} s, so its findings are missing from this review.") from None
    try:
        report = json.loads(result.stdout) if result.returncode in (0, 1) else None
    except ValueError:
        report = None
    if not isinstance(report, list):
        raise ActionlintError(f"actionlint stopped with exit code {result.returncode} and no report.")
    issues = []
    for issue in report:
        if not isinstance(issue, dict):
            continue
        path = paths.get(os.path.basename(str(issue.get("filepath") or "")))
        line = issue.get("line")
        if path is None or not isinstance(line, int) or not issue.get("kind"):
            continue
        issues.append({"path": path, "kind": str(issue["kind"]), "message": str(issue.get("message") or ""), "line": line})
    return issues
