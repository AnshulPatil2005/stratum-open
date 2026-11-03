"""Bandit, Python's security linter, when it's installed (the `bandit` extra).

Bandit reads whole files, so it runs only on files whose full content the
caller passed in `contents`. Of what it reports, only issues on lines the PR
adds are kept. By default only ones it rates medium or high on both severity
and confidence: its low-severity tests (an `assert`, importing `subprocess`)
fire all over most codebases and would bury the rest. `keep_low` keeps them,
for reviews that want everything. Test files are skipped.

It runs as its own process with a time limit, so a file that sends Bandit
into a long parse can't hold up the review.
"""

from __future__ import annotations

import importlib.util
import json
import os
import subprocess
import sys
import tempfile
from typing import Any

from stratum_open.checks.common import is_test_path
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


class BanditTimeout(RuntimeError):
    pass


def available() -> bool:
    return importlib.util.find_spec("bandit") is not None


def check(files: list[FileDiff], contents: dict[str, str] | None = None, *, keep_low: bool = False) -> list[Finding]:
    if not contents or not available():
        return []
    targets = [
        file for file in files
        if file.path.endswith(".py") and file.path in contents and file.status != "deleted"
        and not is_test_path(file.path) and file.added()
    ]
    targets = sorted(targets, key=lambda file: -file.additions)[:MAX_FILES]
    if not targets:
        return []
    added = {file.path: {number for number, _ in file.added()} for file in targets}
    levels = {**_SEVERITY, **(_LOW if keep_low else {})}
    findings = []
    for issue in run_bandit({file.path: contents[file.path] for file in targets}):
        severity = levels.get((issue["severity"], issue["confidence"]))
        mine = added.get(issue["path"], set())
        lines = [issue["line"]] + [n for n in issue["lines"] if n != issue["line"]]
        on_added = [n for n in lines if n in mine]
        if severity is None or not on_added:
            continue
        test_id, name = issue["test_id"], issue["test_name"].replace("_", " ")
        findings.append(Finding(
            title=f"Bandit {test_id}: {name}",
            detail=f"Bandit flags this line: {issue['text']}",
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
            result = subprocess.run(
                [sys.executable, "-m", "bandit", "-f", "json", "-q", *[os.path.join(folder, name) for name in paths]],
                capture_output=True, text=True, encoding="utf-8", errors="replace", timeout=timeout,
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
