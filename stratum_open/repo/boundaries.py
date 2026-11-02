"""Module rules from `.stratum/policy.yml`. With

    boundaries:
      modules: {api: ["server/api/**"], db: ["server/db/**"]}
      forbid: ["api -> db"]

code in `api` must not import `db`. A file belongs to the first module whose
globs match it. Each import a PR adds to a file in a module with a rule is
resolved the way `graph` resolves it, against the default branch's checkout
plus the files the PR adds, and an import the checkout can't place is let go.

Without a checkout, an import is resolved against the PR's own files and,
failing that, from its text alone: each path it could name is matched to the
modules, and it counts when all of those that land in a module land in the
same one. That catches `from server.db import x` and `../db/x`, but a package
from outside the repo that shares a module folder's name can fool it.

Only the line that names the module is checked, and only if the PR adds it:
an import that was already there isn't this PR's doing.
"""

from __future__ import annotations

from dataclasses import replace
from typing import Iterator

from stratum_open.diff import FileDiff
from stratum_open.findings import Finding
from stratum_open.policy import POLICY_PATH, Policy, forbidden_pairs, matches_any
from stratum_open.repo.graph import Import, Resolver, code_files, parse_imports

RULE = "boundaries/forbidden"
Modules = tuple[tuple[str, tuple[str, ...]], ...]


def boundary_findings(files: list[FileDiff], policy: Policy, repo_dir: str | None) -> list[Finding]:
    modules = policy.boundaries.modules
    forbidden = set(forbidden_pairs(policy.boundaries))
    ruled = {source for source, _ in forbidden}
    checked = [f for f in files if f.status != "deleted" and not f.binary and _module(f.path, modules) in ruled]
    if not checked:
        return []

    present = {f.path for f in files if f.status != "deleted"}
    if repo_dir:
        gone = {f.path for f in files if f.status == "deleted"} | {f.old_path for f in files if f.status == "renamed" and f.old_path}
        resolver = Resolver((set(code_files(repo_dir)) - gone) | present)
    else:
        resolver = Resolver(present, roots={path.partition("/")[0] for path in present if "/" in path})

    findings = []
    for file in checked:
        source = _module(file.path, modules)
        flagged: set[tuple[int, str]] = set()
        for imp in added_imports(file):
            for target, what, guessed in _targets(resolver, file.path, imp, modules, guess=not repo_dir):
                if target == source or (source, target) not in forbidden or (imp.line, target) in flagged:
                    continue
                flagged.add((imp.line, target))
                findings.append(Finding(
                    title=f"`{source}` must not import `{target}`",
                    detail=(
                        f"`{source}` must not import `{target}` (rule `{source} -> {target}` in {POLICY_PATH}). "
                        f"This line imports {what}, which is in `{target}`."
                    ),
                    severity="medium",
                    category="maintainability",
                    path=file.path,
                    line_start=imp.line,
                    line_end=imp.line,
                    suggestion=(
                        f"Reach `{target}` through code that `{source}` may depend on, or change the rule in "
                        f"{POLICY_PATH} if `{source}` should now use `{target}` directly."
                    ),
                    source="check",
                    rule=RULE,
                    confidence=0.6 if guessed else 0.9,
                ))
    return findings


def added_imports(file: FileDiff) -> list[Import]:
    """Imports whose module is named on a line the PR adds. Each stretch of
    lines the diff shows is read whole, so a JS import split over lines is
    still seen when its `from '…'` line is added."""
    added = {number for number, _ in file.added()}
    if not added:
        return []
    found = []
    for start, lines in _stretches(file.new_side()):
        for imp in parse_imports(file.path, "\n".join(lines)):
            number = start + imp.line - 1
            if number in added:
                found.append(replace(imp, line=number))
    return found


def _targets(resolver: Resolver, importer: str, imp: Import, modules: Modules, *, guess: bool) -> list[tuple[str, str, bool]]:
    """(module, what's imported, guessed?) for each thing the import loads
    that lands in a module."""
    resolved = resolver.resolve(importer, imp)
    if resolved or not guess:
        return [(module, f"`{path}`", False) for path in resolved if (module := _module(path, modules))]
    found = []
    for group in resolver.candidates(importer, imp):
        landing = {_module(path, modules) for path in group} - {None}
        if len(landing) == 1:
            found.append((landing.pop(), f"`{'.' * imp.level}{imp.module}`", True))
    return found


def _module(path: str, modules: Modules) -> str | None:
    return next((name for name, globs in modules if matches_any(path, globs)), None)


def _stretches(lines: dict[int, str]) -> Iterator[tuple[int, list[str]]]:
    """Runs of consecutive line numbers: (first number, their text)."""
    start, run = 0, []
    for number in sorted(lines):
        if run and number != start + len(run):
            yield start, run
            run = []
        if not run:
            start = number
        run.append(lines[number])
    if run:
        yield start, run
