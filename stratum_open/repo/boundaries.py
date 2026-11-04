"""Module rules from `.stratum/policy.yml`. With

    boundaries:
      modules: {api: ["server/api/**"], db: ["server/db/**"]}
      forbid: ["api -> db: Go through the service layer."]

code in `api` must not import `db`. Either side of a rule may be `*`, any
module: `api -> *` keeps `api` to itself, `* -> db` keeps everyone out of
`db`. A file belongs to the first module whose globs match it. Each import a
PR adds to a file in a module with a rule is resolved the way `graph`
resolves it, against the default branch's checkout plus the files the PR
adds, and an import the checkout can't place is let go.

`new_edges: warn` (or `fail`) also flags a module importing one it didn't
import anywhere on the default branch, a low (or high) finding. The default
branch's edges come from the checkout; with no `modules` set, each top
folder is a module (`src/<name>` and the like count two levels). Without a
checkout, or one too big to read whole, there's nothing to compare with, so
it says nothing.

Without a checkout, a forbidden import is resolved against the PR's own
files and, failing that, from its text alone: each path it could name is
matched to the modules, and it counts when all of those that land in a module
land in the same one. That catches `from server.db import x` and `../db/x`,
but a package from outside the repo that shares a module folder's name can
fool it.

Only the line that names the module is checked, and only if the PR adds it:
an import that was already there isn't this PR's doing, and neither is one
the PR only reformats (removed and added back). One finding per module a file
newly imports, naming the other lines that import it.
"""

from __future__ import annotations

from concurrent.futures import ThreadPoolExecutor
from dataclasses import replace
from typing import Callable, Iterator

from stratum_open.diff import FileDiff
from stratum_open.findings import Finding
from stratum_open.policy import POLICY_PATH, Policy, forbid_rules, matches_any
from stratum_open.repo.graph import MAX_FILES, Import, Resolver, _minified, _read, code_files, parse_imports

RULE = "boundaries/forbidden"
NEW_EDGE_RULE = "boundaries/new-edge"
NESTED_ROOTS = ("src", "lib", "packages", "apps", "services")  # folders whose children are the modules
Modules = tuple[tuple[str, tuple[str, ...]], ...]


def boundary_findings(files: list[FileDiff], policy: Policy, repo_dir: str | None) -> list[Finding]:
    settings = policy.boundaries
    rules = forbid_rules(settings)
    new_edges = settings.new_edges != "off" and repo_dir is not None
    modules = settings.modules
    module_of: Callable[[str], str | None] = (lambda path: _module(path, modules)) if modules else _top_module
    if not rules and not new_edges:
        return []
    ruled = {source for source, _, _ in rules}
    checked = [
        f for f in files
        if f.status != "deleted" and not f.binary and (source := module_of(f.path))
        and (new_edges or source in ruled or "*" in ruled)
    ]
    if not checked:
        return []

    present = {f.path for f in files if f.status != "deleted"}
    baseline: set[tuple[str, str]] | None = None
    if repo_dir:
        gone = {f.path for f in files if f.status == "deleted"} | {f.old_path for f in files if f.status == "renamed" and f.old_path}
        checkout = code_files(repo_dir)
        resolver = Resolver((set(checkout) - gone) | present)
        if new_edges and len(checkout) < MAX_FILES:  # a checkout read only in part can't say what's new
            baseline = _edges(repo_dir, checkout, module_of)
    else:
        resolver = Resolver(present, roots={path.partition("/")[0] for path in present if "/" in path})

    findings = []
    for file in checked:
        source = module_of(file.path)
        before = _removed_imports(file)
        by_target: dict[str, list[tuple[int, str, bool]]] = {}
        for imp in added_imports(file):
            if (imp.module, imp.level) in before:
                continue  # the PR only reformatted it
            for target, what, guessed in _targets(resolver, file.path, imp, module_of, guess=not repo_dir):
                if target != source:
                    by_target.setdefault(target, []).append((imp.line, what, guessed))
        for target, hits in by_target.items():
            line, what, guessed = hits[0]
            others = sorted({number for number, _, _ in hits} - {line})
            also = (f" So do lines {', '.join(map(str, others))}." if len(others) > 1
                    else f" So does line {others[0]}." if others else "")
            rule = next(((a, b, why) for a, b, why in rules if a in (source, "*") and b in (target, "*")), None)
            if rule is not None:
                findings.append(_forbidden(file, source, target, rule, line, what, also, guessed, settings.severity))
            elif baseline is not None and (source, target) not in baseline:
                findings.append(_new_edge(file, source, target, line, what, also, settings.new_edges == "fail"))
    return findings


def _forbidden(file: FileDiff, source: str, target: str, rule: tuple[str, str, str], line: int, what: str, also: str,
               guessed: bool, severity: str) -> Finding:
    a, b, why = rule
    return Finding(
        title=f"`{source}` must not import `{target}`",
        detail=(
            f"`{source}` must not import `{target}` (rule `{a} -> {b}` in {POLICY_PATH})." + (f" Why: {why}" if why else "")
            + f" This line imports {what}, which is in `{target}`.{also}"
        ),
        severity=severity,
        category="maintainability",
        path=file.path,
        line_start=line,
        line_end=line,
        suggestion=(
            f"Reach `{target}` through code that `{source}` may depend on, or change the rule in "
            f"{POLICY_PATH} if `{source}` should now use `{target}` directly."
        ),
        source="check",
        rule=RULE,
        confidence=0.6 if guessed else 0.9,
    )


def _new_edge(file: FileDiff, source: str, target: str, line: int, what: str, also: str, fail: bool) -> Finding:
    return Finding(
        title=f"New dependency: `{source}` now imports `{target}`",
        detail=(
            f"Nothing in `{source}` imports `{target}` on the default branch. This line imports {what}, "
            f"which is in `{target}`.{also} A new link between modules is easy to add and hard to take back."
        ),
        severity="high" if fail else "low",
        category="maintainability",
        path=file.path,
        line_start=line,
        line_end=line,
        suggestion=(
            f"If `{source}` should depend on `{target}`, there's nothing to do. If not, reach it through code "
            f"`{source}` already uses, or add `{source} -> {target}` to `boundaries.forbid` in {POLICY_PATH}."
        ),
        source="check",
        rule=NEW_EDGE_RULE,
        confidence=0.9,
        needs_human=fail,  # whether two modules may depend on each other is a maintainer's call
    )


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


def _removed_imports(file: FileDiff) -> set[tuple[str, int]]:
    """(module, level) of each import named on a line the PR removes."""
    old_side = {line.old: line.text for hunk in file.hunks for line in hunk.lines if line.old is not None}
    removed = {line.old for hunk in file.hunks for line in hunk.lines if line.kind == "-"}
    if not removed:
        return set()
    found = set()
    for start, lines in _stretches(old_side):
        for imp in parse_imports(file.path, "\n".join(lines)):
            if start + imp.line - 1 in removed:
                found.add((imp.module, imp.level))
    return found


def _edges(repo_dir: str, files: list[str], module_of: Callable[[str], str | None]) -> set[tuple[str, str]]:
    """Every (module, module it imports) on the default branch."""
    resolver = Resolver(files)
    edges: set[tuple[str, str]] = set()
    with ThreadPoolExecutor(max_workers=8) as pool:
        for at in range(0, len(files), 256):
            batch = files[at:at + 256]
            for path, text in zip(batch, pool.map(lambda p: _read(repo_dir, p), batch)):
                source = module_of(path)
                if source is None or text is None or _minified(text):
                    continue
                for imp in parse_imports(path, text):
                    for target in resolver.resolve(path, imp):
                        if (module := module_of(target)) and module != source:
                            edges.add((source, module))
    return edges


def _targets(resolver: Resolver, importer: str, imp: Import, module_of: Callable[[str], str | None], *,
             guess: bool) -> list[tuple[str, str, bool]]:
    """(module, what's imported, guessed?) for each thing the import loads
    that lands in a module."""
    resolved = resolver.resolve(importer, imp)
    if resolved or not guess:
        return [(module, f"`{path}`", False) for path in resolved if (module := module_of(path))]
    found = []
    for group in resolver.candidates(importer, imp):
        landing = {module_of(path) for path in group} - {None}
        if len(landing) == 1:
            found.append((landing.pop(), f"`{'.' * imp.level}{imp.module}`", True))
    return found


def _module(path: str, modules: Modules) -> str | None:
    return next((name for name, globs in modules if matches_any(path, globs)), None)


def _top_module(path: str) -> str | None:
    """With no modules set: the top folder, or two levels under `src/` and
    the like. A file at the root is in none."""
    parts = path.split("/")
    if len(parts) < 2:
        return None
    return "/".join(parts[:2]) if parts[0] in NESTED_ROOTS and len(parts) > 2 else parts[0]


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
