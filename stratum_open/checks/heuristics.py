"""File-level heuristics: a finding for each changed file in an area where a
small mistake costs the most (auth, migrations, CI, dependencies), for tests
that lose more lines than they gain, and for a PR that says it breaks things.

They flag where to look, not a defect, so they're off unless
`checks.heuristics` is on (the `large` profile turns it on), and the high ones
send the PR to a maintainer (`needs_human`) rather than back to its author.
Weakened tests are the exception: the author can restore them. Secrets, eval and
shell calls, and SQL built from strings are left to the pattern checks, which
find them on the exact line.
"""

from __future__ import annotations

from stratum_open.areas import areas
from stratum_open.diff import FileDiff
from stratum_open.findings import Finding


def check(files: list[FileDiff], pr_text: str = "") -> list[Finding]:
    """`pr_text`: the PR's title and description."""
    findings: list[Finding] = []
    for file in files:
        found = areas(file.path)
        changed = file.additions + file.deletions
        if "tests" in found and file.deletions > file.additions:
            findings.append(_finding(
                file, "tests-weakened", "tests", "high", "Tests removed or weakened", False,
                "This file removes more test coverage than it adds, which raises regression risk.",
                "Add or update tests that cover the changed behavior before merging.",
            ))
        if "dependency" in found and changed:
            findings.append(_finding(
                file, "dependencies-changed", "breaking-change", "medium", "Dependency surface changed", False,
                "Dependency files changed and may alter runtime behavior, transitive vulnerabilities, or APIs.",
                "Review changelogs and pin or verify versions with focused smoke tests.",
            ))
        if "workflow" in found:
            findings.append(_finding(
                file, "workflow-changed", "maintainability", "medium", "CI or deployment workflow changed", False,
                "Workflow changes can affect release safety, required checks, or secret handling.",
                "Validate job triggers, permissions, and required checks against expected branch rules.",
            ))
        if "auth" in found:
            findings.append(_finding(
                file, "auth-path", "security", "high", "Authentication-sensitive code changed", True,
                "The PR touches authentication or permission paths where small logic errors can have broad impact.",
                "Add targeted auth tests and review token, session, and permission edge cases.",
            ))
        if "config" in found and any("cors" in line.text.lower() for hunk in file.hunks for line in hunk.lines if line.kind in "+-"):
            findings.append(_finding(
                file, "cors-config", "security", "high", "CORS or runtime security settings changed", True,
                "CORS or environment-sensitive configuration changed and may widen access unexpectedly.",
                "Verify allowed origins, credentials policy, and environment-specific defaults.",
            ))
        if "migration" in found:
            findings.append(_finding(
                file, "migration", "breaking-change", "high", "Schema or migration change detected", True,
                "Schema-affecting changes can break compatibility or data assumptions if rollout is not coordinated.",
                "Document migration order, backfill needs, and rollback behavior.",
            ))
    if "breaking change" in pr_text.lower():
        findings.append(Finding(
            title="Breaking change called out in PR text",
            detail="The PR mentions a breaking change and should surface compatibility expectations explicitly.",
            severity="medium", category="breaking-change", path="",
            suggestion="Add migration notes, versioning guidance, or consumer rollout steps.",
            source="check", rule="heuristic/breaking-change", confidence=0.75, needs_human=True,
        ))
    return findings


def _finding(file: FileDiff, rule: str, category: str, severity: str, title: str, maintainer: bool, detail: str,
             suggestion: str) -> Finding:
    return Finding(title=title, detail=detail, severity=severity, category=category, path=file.path, suggestion=suggestion,
                   source="check", rule=f"heuristic/{rule}", confidence=0.75, needs_human=maintainer)
