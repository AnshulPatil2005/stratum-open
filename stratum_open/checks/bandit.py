"""Bandit, Python's security linter, when it's installed (the `bandit` extra).

Bandit reads whole files, so it runs only on files whose full content the
caller passed in `contents`. Of what it reports, only issues on lines the PR
adds are kept. By default only ones it rates medium or high on both severity
and confidence: its low-severity tests (an `assert`, importing `subprocess`)
fire all over most codebases and would bury the rest. `keep_low` keeps them,
for reviews that want everything. Test files are skipped.

Bandit runs with `--ignore-nosec`. The files are the PR's own versions, so a
`# nosec` on an added line was written by the PR's author, and following it
would let the author decide what Bandit reports. A finding on a line marked
that way says so, so the reviewer sees the author meant to skip it. A `# nosec`
already on the default branch doesn't matter, since only added lines count.

It runs as its own process with a time limit, so a file that sends Bandit
into a long parse can't hold up the review.
"""

from __future__ import annotations

import importlib.util
import json
import os
import re
import subprocess
import sys
import tempfile
from typing import Any

from stratum_open.checks.common import checked_lines, is_test_path, moved_in
from stratum_open.diff import FileDiff
from stratum_open.findings import Finding

MAX_FILES = 20
TIMEOUT_SECONDS = 15
# (Bandit's severity, its confidence) -> ours. Anything else is dropped,
# unless `keep_low`, which also keeps LOW_RESULTS.
_SEVERITY = {
    ("HIGH", "HIGH"): "high",
    ("HIGH", "MEDIUM"): "medium",
    ("MEDIUM", "HIGH"): "medium",
    ("MEDIUM", "MEDIUM"): "low",
}
_LOW = {
    ("HIGH", "LOW"): "low", ("MEDIUM", "LOW"): "low",
    ("LOW", "HIGH"): "low", ("LOW", "MEDIUM"): "low", ("LOW", "LOW"): "low",
}
_CONFIDENCE = {"HIGH": 0.9, "MEDIUM": 0.6, "LOW": 0.4}
# How Bandit spots a `# nosec` comment (bandit.core.manager.NOSEC_COMMENT).
_NOSEC = re.compile(r"#\s*nosec")


class BanditTimeout(RuntimeError):
    pass


def available() -> bool:
    return importlib.util.find_spec("bandit") is not None


def scope(path: str) -> frozenset[str]:
    """Bandit reads Python files outside the tests."""
    return frozenset({"bandit"}) if path.endswith(".py") and not is_test_path(path) else frozenset()


def plan(files: list[FileDiff], contents: dict[str, str] | None) -> tuple[list[str], list[str]]:
    """The Python files Bandit reads, and the ones it would read but doesn't:
    past MAX_FILES, or without their text. Files the PR edits come first,
    biggest change first, then files moved here from the tests, biggest
    first: a PR padded with moves can't push its own new code out."""
    contents = contents or {}
    candidates = [
        file for file in files
        if scope(file.path) and file.status != "deleted" and not file.binary and (file.added() or moved_in(file, scope))
    ]

    def rank(file: FileDiff) -> tuple[bool, int]:
        moved = moved_in(file, scope)
        return moved, -(len(checked_lines(file, contents, scope)) if moved else file.additions)

    read = [file.path for file in sorted((f for f in candidates if f.path in contents), key=rank)[:MAX_FILES]]
    return read, [file.path for file in candidates if file.path not in read]


def check(files: list[FileDiff], contents: dict[str, str] | None = None, *, keep_low: bool = False) -> list[Finding]:
    if not contents or not available():
        return []
    targets, _ = plan(files, contents)
    if not targets:
        return []
    # A file moved here from the tests counts every line as added.
    added = {file.path: dict(checked_lines(file, contents, scope)) for file in files if file.path in targets}
    levels = {**_SEVERITY, **(_LOW if keep_low else {})}
    findings = []
    for issue in run_bandit({path: contents[path] for path in targets}):
        severity = levels.get((issue["severity"], issue["confidence"]))
        mine = added.get(issue["path"], {})
        lines = [issue["line"]] + [n for n in issue["lines"] if n != issue["line"]]
        on_added = [n for n in lines if n in mine]
        if severity is None or not on_added:
            continue
        test_id, name = issue["test_id"], issue["test_name"].replace("_", " ")
        detail = f"Bandit flags this line: {issue['text']}"
        if any(_NOSEC.search(mine[n]) for n in on_added):
            detail += " The PR marks it `# nosec`. Stratum reports it anyway, because the PR's author added that comment."
        findings.append(Finding(
            title=f"Bandit {test_id}: {name}",
            detail=detail,
            severity=severity,
            category="security",
            path=issue["path"],
            line_start=on_added[0],
            line_end=on_added[0],
            suggestion=f"If it's safe here, say why in a comment. Bandit's documentation for {test_id} explains the risk.",
            source="check",
            rule=f"bandit/{test_id}",
            confidence=_CONFIDENCE.get(issue["confidence"], 0.6),
        ))
    return findings


def run_bandit(sources: dict[str, str], *, timeout: float = TIMEOUT_SECONDS) -> list[dict[str, Any]]:
    """Bandit's issues for each file's text, as plain dicts keyed by our
    path. Files go in a temporary folder, since Bandit reads from disk, and
    Bandit runs once over all of them in its own process."""
    with tempfile.TemporaryDirectory(prefix="stratum-bandit-") as folder:
        paths: dict[str, str] = {}
        for index, (path, text) in enumerate(sources.items()):
            name = f"{index}.py"
            # newline="" writes the line endings as they are, keeping line numbers.
            with open(os.path.join(folder, name), "w", encoding="utf-8", errors="replace", newline="") as handle:
                handle.write(text)
            paths[name] = path
        try:
            # `python -m` looks for the module in the working folder first, and the
            # review's working folder can be a checkout of the code under review:
            # a bandit.py there would run in Bandit's place. -P keeps the working
            # folder off the path, and the run starts in the temporary folder, which
            # holds only the numbered files.
            result = subprocess.run(
                [sys.executable, "-P", "-m", "bandit", "-f", "json", "-q", "--ignore-nosec", *[os.path.join(folder, name) for name in paths]],
                capture_output=True, text=True, encoding="utf-8", errors="replace", timeout=timeout, cwd=folder,
            )
        except subprocess.TimeoutExpired:
            raise BanditTimeout(f"Bandit took longer than {timeout:.0f} s, so its findings are missing from this review.") from None
        try:
            report = json.loads(result.stdout or "{}")
        except ValueError:
            return []
    issues = []
    for issue in report.get("results", []):
        path = paths.get(os.path.basename(issue.get("filename", "")))
        if path is None:
            continue
        issues.append({
            "path": path,
            "test_id": issue.get("test_id", ""),
            "test_name": issue.get("test_name", ""),
            "severity": issue.get("issue_severity", "MEDIUM"),
            "confidence": issue.get("issue_confidence", "MEDIUM"),
            "text": issue.get("issue_text", ""),
            "line": issue.get("line_number"),
            "lines": list(issue.get("line_range") or ()),
        })
    return issues
