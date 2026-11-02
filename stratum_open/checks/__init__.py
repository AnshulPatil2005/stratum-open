"""Deterministic checks: problems found by rules over a PR's diff, with no
model call. They run on every PR, whatever the model does, and give the same
answer every time, so their findings are the ones a maintainer can rely on.

Every finding has `source="check"`, a stable `rule` id and, where it's about
a line, the new-file number of the added line it's about.
"""

from __future__ import annotations

from typing import Callable

from stratum_open.checks import agents, bandit, dependencies, paths, patterns, workflows
from stratum_open.checks.dependencies import PackageInfo, PackageLookup
from stratum_open.diff import FileDiff
from stratum_open.findings import Finding
from stratum_open.policy import Policy

__all__ = ["PackageInfo", "PackageLookup", "run_checks"]


def run_checks(
    files: list[FileDiff],
    policy: Policy,
    *,
    lookup: PackageLookup | None = None,
    contents: dict[str, str] | None = None,
) -> list[Finding]:
    """Every check over the PR's files. `lookup` asks package registries
    about new dependencies (only when `dependencies.registry_lookup` is on);
    `contents` maps a path to its full new text, when the caller has it, and
    lets the checks see past the diff's context.

    One check failing never stops the others: it becomes a low finding
    saying which check didn't run."""
    contents = contents or {}
    registry = lookup if policy.dependencies.registry_lookup else None
    checks: list[tuple[str, Callable[[], list[Finding]]]] = [
        ("protected paths", lambda: paths.check(files, policy)),
        ("risky code patterns", lambda: patterns.check(files, contents)),
        ("workflow", lambda: workflows.check(files, contents)),
        ("coding-agent files", lambda: agents.check_instruction_files(files)),
        ("hidden Unicode", lambda: agents.check_hidden_unicode(files)),
        ("dependency", lambda: dependencies.check(files, policy, lookup=registry, contents=contents)),
        ("Bandit", lambda: bandit.check(files, contents)),
    ]
    findings: list[Finding] = []
    for name, run in checks:
        try:
            findings.extend(run())
        except Exception as exc:  # one broken check mustn't cost the review the others
            message = f"{type(exc).__name__}: {exc}"[:200]
            findings.append(Finding(
                title=f"The {name} check didn't run",
                detail=f"The {name} check failed with an error ({message}), so this review is missing its findings. The other checks ran.",
                severity="low",
                category="workflow",
                suggestion="Re-run the review. If it fails again, please report it with this message.",
                source="check",
                rule="checks/error",
                confidence=1.0,
            ))
    return findings
