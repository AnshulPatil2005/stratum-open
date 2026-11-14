"""actionlint on the GitHub workflows a PR adds or changes, when
`checks.actionlint` is on and `actionlint` is on PATH (GitHub's runners don't
have it: the README says how to add it).

It finds what makes a workflow fail or do something other than it says: YAML
GitHub can't load, a key it doesn't know, a `needs:` naming no job, an
expression naming a matrix key or a property that isn't there, an input a
well-known action doesn't take. Only issues on lines the PR adds are kept,
from the workflow's full text in `contents`.

Some of what it reports is left out:
- a runner label it doesn't know: a self-hosted runner's labels are listed in
  the project's own actionlint config, which isn't read, and GitHub adds new
  hosted labels (`ubuntu-26.04`) faster than actionlint learns them;
- issue or PR text in a script (`${{ github.event.pull_request.title }}` in
  `run:`), which the workflow check (workflows.py) already reports;
- `uses: $/...`, an action of the same repository, which GitHub added after
  actionlint 1.7.12 and which it calls malformed;
- a YAML anchor defined and not used, which is tidiness, not a bug.

actionlint can run ShellCheck on each `run:` script, and here it doesn't. On
the merged workflows of the benchmark that found nothing right, and dozens of
false alarms: PowerShell steps of a job whose shell it couldn't tell, and
`${{ }}` it replaces with a placeholder, which then looks like a constant
(benchmark/results/2026-11-14-shell-checks). Its Python check (pyflakes) is
off too.

No code from the PR runs, and the PR can't steer actionlint. It reads the
YAML and never runs a step. The files go in a temporary folder outside any
checkout, under names chosen here, with an empty config file of its own, so
the project's `actionlint.yaml` isn't read either: like the workflow check,
it judges the workflow as the PR's author wrote it. Local actions and
reusable workflows the PR names aren't there, and actionlint skips what it
would check about them. It runs as its own process with a time limit.

What actionlint knows of GitHub is what GitHub had when it was released, so a
key, a permission or a property GitHub adds later is reported as unknown
until actionlint catches up.
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
    "shellcheck": "ShellCheck isn't run on the scripts",
    "pyflakes": "pyflakes isn't run on the scripts",
}
# Messages that are left out, whatever their kind.
_LEFT_OUT = re.compile(
    r"is potentially untrusted"  # the workflow check reports these
    r'|"\$/'  # `uses: $/path`, which GitHub added after actionlint 1.7.12
    r"|^anchor \S+ is defined but not used"
)
# What stops the workflow, or the job, from running at all: YAML that doesn't load, a `needs:` with no such job.
_FAILS = re.compile(r"^could not parse as YAML|^job \S+ needs job")
# What actionlint calls unknown may be something GitHub added after its release.
_UNKNOWN = re.compile(r"\bunknown\b|unexpected key|is not defined in object type")


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
    return [finding for issue in run_actionlint({path: contents[path] for path in targets})
            if (finding := to_finding(issue, added.get(issue["path"], set()))) is not None]


def to_finding(issue: dict[str, Any], added: set[int]) -> Finding | None:
    """The finding for one of actionlint's issues, or None when it's left out
    or not on a line in `added`."""
    kind, message = issue["kind"], issue["message"]
    if kind in DROPPED or _LEFT_OUT.search(message) or issue["line"] not in added:
        return None
    fails = bool(_FAILS.search(message))
    detail = f"actionlint reports {shellcheck.quote(message)}. "
    detail += "GitHub won't run the workflow, or this job, as written." if fails else "The workflow doesn't do what this line says."
    if _UNKNOWN.search(message):
        detail += " actionlint knows GitHub as it was when it was released, so if GitHub added this lately, the line is fine."
    return Finding(
        title=f"actionlint ({kind}): {_title(message)}",
        detail=detail,
        severity="high" if fails else "medium",
        category="bug",
        path=issue["path"],
        line_start=issue["line"],
        line_end=issue["line"],
        suggestion=f"actionlint's list of checks explains the `{kind}` one: https://github.com/rhysd/actionlint/blob/main/docs/checks.md",
        source="check",
        rule=f"actionlint/{kind}",
        confidence=0.9,
    )


def _title(message: str) -> str:
    first = message.replace("`", "'").replace("\n", " ").split(". ", 1)[0].rstrip(".")
    return "`" + (first[:77] + "..." if len(first) > 80 else first) + "`"


def run_actionlint(sources: dict[str, str], *, scripts: str = "", timeout: float = TIMEOUT_SECONDS) -> list[dict[str, Any]]:
    """actionlint's issues for each workflow's text, as plain dicts keyed by
    our path. The files are written as `.github/workflows/<n>.yml` in a
    temporary folder, and actionlint runs once over all of them. `scripts`,
    a ShellCheck to run on the `run:` scripts, is for the benchmark, which
    measured what that would add. Raises ActionlintError when actionlint
    isn't there, doesn't finish, or can't be read."""
    program = find_program("actionlint")
    if program is None:
        raise ActionlintError("actionlint isn't on PATH any more.")
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
        # An empty -shellcheck and -pyflakes turn those off, whatever is on PATH.
        command = [
            program, "-no-color", "-format", "{{json .}}", "-config-file", config, f"-shellcheck={scripts}", "-pyflakes=",
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
