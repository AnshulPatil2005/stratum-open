"""Bandit, Python's security linter, when it's installed (the `bandit` extra).

Bandit reads whole files, so it runs only on files whose full content the
caller passed in `contents`. Of what it reports, only issues on lines the PR
adds are kept, and only ones it rates medium or high on both severity and
confidence: its low-severity tests (an `assert`, importing `subprocess`)
fire all over most codebases and would bury the rest. Test files are
skipped for the same reason.
"""

from __future__ import annotations

import importlib.util
import os
import tempfile
from typing import Any

from stratum_open.checks.common import is_test_path
from stratum_open.diff import FileDiff
from stratum_open.findings import Finding

MAX_FILES = 20
# (Bandit's severity, its confidence) -> ours. Anything else is dropped.
_SEVERITY = {
    ("HIGH", "HIGH"): "high",
    ("HIGH", "MEDIUM"): "medium",
    ("MEDIUM", "HIGH"): "medium",
    ("MEDIUM", "MEDIUM"): "low",
}
_CONFIDENCE = {"HIGH": 0.9, "MEDIUM": 0.6}


def available() -> bool:
    return importlib.util.find_spec("bandit") is not None


def check(files: list[FileDiff], contents: dict[str, str] | None = None) -> list[Finding]:
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
    findings = []
    for issue in run_bandit({file.path: contents[file.path] for file in targets}):
        severity = _SEVERITY.get((issue["severity"], issue["confidence"]))
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


def run_bandit(sources: dict[str, str]) -> list[dict[str, Any]]:
    """Bandit's issues for each file's text, as plain dicts keyed by our
    path. Files go in a temporary folder, since Bandit reads from disk."""
    from bandit.core import config, manager  # an optional extra: imported only when used

    with tempfile.TemporaryDirectory(prefix="stratum-bandit-") as folder:
        paths: dict[str, str] = {}
        for index, (path, text) in enumerate(sources.items()):
            name = f"{index}.py"
            # newline="" writes the line endings as they are, keeping line numbers.
            with open(os.path.join(folder, name), "w", encoding="utf-8", errors="replace", newline="") as handle:
                handle.write(text)
            paths[name] = path
        runner = manager.BanditManager(config.BanditConfig(), "file", quiet=True)
        runner.discover_files([os.path.join(folder, name) for name in paths])
        runner.run_tests()
        issues = []
        for issue in runner.get_issue_list():
            path = paths.get(os.path.basename(issue.fname))
            if path is None:
                continue
            issues.append({
                "path": path,
                "test_id": issue.test_id,
                "test_name": issue.test or "",
                "severity": issue.severity,
                "confidence": issue.confidence,
                "text": issue.text,
                "line": issue.lineno,
                "lines": list(issue.linerange or ()),
            })
        return issues
