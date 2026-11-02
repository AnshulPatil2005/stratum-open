"""A finding: one problem on one spot of a PR, from the model or from a check.

Every finding gets a fingerprint from what it's about (its rule, file and the
code on its lines), not from where it sits, so the same problem keeps the same
fingerprint when lines move. Inline comments, resolved threads, `@stratum
ignore` and 👍/👎 feedback all key on it.
"""

from __future__ import annotations

import hashlib
import re
from dataclasses import asdict, dataclass, fields
from typing import Any, Iterable

from stratum_open.diff import FileDiff

SEVERITIES = ("low", "medium", "high", "critical")
CATEGORIES = ("bug", "security", "performance", "maintainability", "tests", "breaking-change", "supply-chain", "workflow")
_RANK = {name: index for index, name in enumerate(SEVERITIES)}
_WS = re.compile(r"\s+")


@dataclass
class Finding:
    title: str
    detail: str
    severity: str = "medium"
    category: str = "bug"
    path: str = ""  # "" for the PR as a whole
    line_start: int | None = None  # new-file lines; None = the whole file
    line_end: int | None = None
    suggestion: str = ""  # what to do, in a sentence or two
    replacement: str | None = None  # exact new code for line_start..line_end
    source: str = "model"  # "model" | "check"
    rule: str = ""  # a stable id: a check's name, or the model's title as a slug
    confidence: float = 0.7
    needs_human: bool = False  # forces the "needs a maintainer" label
    fingerprint: str = ""

    def __post_init__(self) -> None:
        self.severity = self.severity if self.severity in SEVERITIES else "medium"
        self.category = self.category if self.category in CATEGORIES else "bug"
        if self.line_start is not None and (self.line_end is None or self.line_end < self.line_start):
            self.line_end = self.line_start
        if not self.rule:
            self.rule = f"{self.source}:{slug(self.title)}"

    @property
    def anchored(self) -> bool:
        return bool(self.path) and self.line_start is not None

    @property
    def blocking(self) -> bool:
        return _RANK[self.severity] >= _RANK["high"]

    def to_dict(self) -> dict[str, Any]:
        return asdict(self)

    @classmethod
    def from_dict(cls, data: dict[str, Any]) -> Finding:
        known = {f.name for f in fields(cls)}
        return cls(**{k: v for k, v in data.items() if k in known})


def slug(text: str, limit: int = 48) -> str:
    return re.sub(r"[^a-z0-9]+", "-", text.lower()).strip("-")[:limit] or "finding"


def severity_rank(severity: str) -> int:
    return _RANK.get(severity, 1)


def fingerprint(finding: Finding, files: dict[str, FileDiff]) -> str:
    """Model findings key on their category rather than their wording, which
    changes from run to run; checks key on their rule."""
    # A check's title is fixed and names what it found (two new packages on one
    # line are two findings); the model's wording isn't, so it keys on category.
    what = f"{finding.rule}:{slug(finding.title)}" if finding.source == "check" else finding.category
    file = files.get(finding.path)
    anchor = ""
    if file is not None and finding.line_start is not None:
        anchor = _WS.sub(" ", file.snippet(finding.line_start, finding.line_end)).strip()
    if not anchor:
        anchor = slug(finding.title)
    key = f"{finding.source}|{what}|{finding.path}|{anchor}".encode("utf-8")
    return hashlib.sha1(key, usedforsecurity=False).hexdigest()[:12]  # an id, not a secret


def assign_fingerprints(findings: Iterable[Finding], files: list[FileDiff]) -> None:
    by_path = {file.path: file for file in files}
    for finding in findings:
        finding.fingerprint = fingerprint(finding, by_path)


def anchor_to_diff(findings: list[Finding], files: list[FileDiff]) -> list[Finding]:
    """Keep a finding's lines only if GitHub can comment there; otherwise it
    becomes a file-level finding listed in the summary. Findings on files the
    PR doesn't touch are dropped: the model can't see those files."""
    by_path = {file.path: file for file in files}
    kept = []
    for finding in findings:
        if finding.path and finding.path not in by_path:
            continue
        file = by_path.get(finding.path)
        if file is not None and finding.line_start is not None and not file.can_comment(finding.line_start, finding.line_end):
            if file.can_comment(finding.line_start):
                finding.line_end = finding.line_start
            else:
                finding.line_start = finding.line_end = None
        if finding.line_start is None:
            finding.replacement = None
        kept.append(finding)
    return kept


def dedupe(findings: list[Finding]) -> list[Finding]:
    """One finding per fingerprint, and one per overlapping lines and
    category: the model often reports a problem twice in different words.
    The most severe, anchored, confident copy wins."""
    kept: list[Finding] = []
    for finding in sorted(findings, key=lambda f: (-severity_rank(f.severity), not f.anchored, -f.confidence)):
        if not any(_same(finding, other) for other in kept):
            kept.append(finding)
    return kept


def _same(a: Finding, b: Finding) -> bool:
    if a.fingerprint and a.fingerprint == b.fingerprint:
        return True
    if a.source == b.source == "check" and a.rule == b.rule:
        return False  # one rule, two things found on the same line
    return (
        a.anchored and b.anchored and a.path == b.path and a.category == b.category
        and a.line_start <= b.line_end and b.line_start <= a.line_end
    )


def rank(findings: list[Finding]) -> list[Finding]:
    return sorted(findings, key=lambda f: (-severity_rank(f.severity), -f.confidence, f.path, f.line_start or 0))


def risk_score(findings: list[Finding]) -> int:
    """0–100 from what was found, the same way every run: the worst severity
    sets the floor and each further finding adds a little."""
    if not findings:
        return 5
    worst = max(severity_rank(f.severity) for f in findings)
    floor = (20, 45, 70, 88)[worst]
    return min(100, floor + 2 * (len(findings) - 1))


def counts(findings: Iterable[Finding]) -> dict[str, int]:
    tally = {name: 0 for name in reversed(SEVERITIES)}
    for finding in findings:
        tally[finding.severity] += 1
    return tally
