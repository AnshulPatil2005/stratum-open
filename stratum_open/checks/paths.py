"""Files the policy says a maintainer always reviews (`always_human.paths`)."""

from __future__ import annotations

from stratum_open.diff import FileDiff
from stratum_open.findings import Finding
from stratum_open.policy import Policy


def check(files: list[FileDiff], policy: Policy) -> list[Finding]:
    findings = []
    for file in files:
        # A rename counts from either side: moving a file out of a protected
        # folder takes its protection away, which needs review too.
        protected = [path for path in (file.path, file.old_path) if path and policy.needs_human(path)]
        if not protected:
            continue
        findings.append(Finding(
            title="Protected path changed",
            detail=(
                f"`{protected[0]}` is protected by this project's policy (`always_human.paths`), "
                "so a maintainer must review this change before it merges."
            ),
            severity="medium",
            category="workflow",
            path=file.path,
            source="check",
            rule="paths/always-human",
            confidence=1.0,
            needs_human=True,
        ))
    return findings
