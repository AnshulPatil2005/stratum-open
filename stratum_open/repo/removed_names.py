"""A name a PR removes that the repository still uses, in Python: the PR
renames `parse_cookie` to `parse_cookies` in `app/http.py`, and
`app/request.py`, which it doesn't touch, still has `from app.http import
parse_cookie`. Nothing in the diff looks wrong, because the line that breaks
isn't in it, and it fails only when that line runs. Finding it needs no
model, and every finding can be checked by opening two files.

This is unknown_names.py read the other way round. That check starts from the
lines a PR adds and looks each name up in its module. This one starts from
the names a PR takes out of a module and looks for the lines that still want
them:

1. In each Python file the PR modifies: the names its top level bound before
   the PR (a `def`, a `class`, an assignment, an import) on a line the diff
   removes, and that the file binds nowhere after the PR.
2. Every Python file of the repository that could be using one of them (its
   text has the name where an import or an attribute would, and the last
   part of the module's name) is then read the way unknown_names.py reads
   an added line, every line of it this time: `from pkg.mod import name`,
   and `mod.name` where `mod` was bound by an import. The repository is read
   as it will be after the PR: the PR's own text for the files it changes,
   so a use the PR fixes or deletes is gone, and the default branch's
   checkout for the rest. (A use on a line the PR itself adds is that
   check's to report, on that line, when it is on too.)

Step 2 is that check's own code, so everything it asks before calling a name
missing is asked here too: the import resolves to one file of the
repository; that file's names can be listed after the PR (no `__getattr__`
that could answer, no star import that can't be followed, nothing reaching
for the module's dict); nothing in the repository sets the name from
outside; and nothing around the line catches the failure or expects it
(`try`, `pytest.raises`, `hasattr`, `TYPE_CHECKING`, an annotation). A name
the module still binds in any branch, imports back from where it moved to,
or answers for in a `__getattr__` that keeps old names alive is still
defined, and is not a finding.

The removal has to be in the diff as well. The file's text before the PR is
the checkout's (or, for a checkout that already holds the change, its text
with the diff undone), and the diff must apply to it: a checkout that has
moved on since the PR branched is not what the PR removed anything from. A
name counts as removed only when a line that bound it is one of the diff's
removed lines.

A finding is placed in the module the name was removed from, on the first
line the diff shows after the removed ones (the new name, when it was
renamed), and names the lines that still use it. GitHub takes a comment only
on a line of the diff, and the line that will fail isn't one.

What that leaves out: a module the PR deletes or moves (a module that isn't
in the checkout may be written by a build, so its absence proves nothing);
methods and attributes of a class; and every use that isn't an import or an
attribute of a module: `from mod import *`, `getattr(mod, "name")`, a name
in a string, a template or a config file, and code outside the repository
that imports it.

Without a checkout nothing is checked. Nothing here imports or runs the
repository's code: files are parsed with `ast`. A PR that removes no name
costs a walk of the checkout and two parses of each file it modifies; only
when one is removed is every Python file's text searched for it.
"""

from __future__ import annotations

import ast
import re
from collections import Counter
from concurrent.futures import ThreadPoolExecutor
from dataclasses import dataclass, field

from stratum_open.diff import FileDiff, Hunk, Line
from stratum_open.findings import Finding
from stratum_open.policy import Policy
from stratum_open.repo.graph import SKIP_DIRS, _minified, _read
from stratum_open.repo.unknown_names import (
    MAX_PR_FILES, _dunder, _failed, _is_init, _Missing, _missing_on, _nearest, _parse, _patched, _Repo, _top_level,
)
from stratum_open.util import count

RULE = "code/removed-name"
MAX_CHECKED = 50  # modified Python files read in one PR, the biggest removals first
MAX_NAMES = 200  # removed names looked for in one PR
MAX_READ = 400  # files that mention one of them and are parsed for its uses
_SHOWN = 3  # use sites named in a finding
_NAMES_ONLY = re.compile(r"[\s\w,()\\]*(?:#.*)?")
_KINDS = {"function": "the function", "class": "the class", "import": "the import of", "name": "the assignment of"}


def removed_name_findings(files: list[FileDiff], policy: Policy, repo_dir: str | None, *, contents: dict[str, str] | None = None,
                          applied: bool = False) -> list[Finding]:
    """`repo_dir`: the default branch's checkout. `contents`: a changed
    file's full new text, where the caller has it; without it the text is
    the checkout's with the diff applied. `applied`: the checkout already
    holds the change (a local review of HEAD).

    Off unless `checks.removed_names` is set. It never raises: an error
    becomes the low finding the other checks give when one of them fails."""
    if not policy.checks.removed_names or not repo_dir or len(files) >= MAX_PR_FILES:
        return []
    try:
        # A use on a line the PR adds is the unknown-names check's to report, on that line, when it's on.
        return _check(files, repo_dir, contents or {}, applied, added=not policy.checks.unknown_names)[0]
    except Exception as exc:  # the review goes on without this check
        return [_failed("removed names", exc)]


def _check(files: list[FileDiff], repo_dir: str, contents: dict[str, str], applied: bool, *, added: bool = True) -> tuple[list[Finding], Counter[str]]:
    """The findings, and how each removed name came out (for the benchmark)."""
    removed, tally = _decide(files, repo_dir, contents, applied, added=added)
    by_path = {file.path: file for file in files}
    findings = [_finding(item, by_path[item.module]) for item in removed if item.outcome == REPORTED]
    return sorted(findings, key=lambda f: (f.path, f.line_start or 0, f.title)), tally


# How a removed name can come out. Only the first is a finding.
REPORTED = "still used: reported"
SET_ELSEWHERE = "still used, and something in the repository may set it from outside: left alone"
UNPROVEN = "another file imports or reads a name like it, in no use that is certain: left alone"
UNREAD = "more files import or read a name like it than are read: left alone"
UNUSED = "no other file imports or reads a name like it: nothing to report"
OPEN = "the module's names can't be listed after the PR: left alone"


@dataclass
class _Removed:
    module: str  # the file the PR removes it from
    name: str
    kind: str  # "function" | "class" | "import" | "name"
    line: int  # the removed line that bound it, in the old file
    added: set[str]  # the names the PR adds to the module: one of them may be what it was renamed to
    uses: list[_Missing] = field(default_factory=list)
    outcome: str = UNUSED
    moved: str = ""  # another file the PR gives a definition of that name: where it may have gone
    why: str = ""  # for OPEN: what keeps the module's names from being listed


def _decide(files: list[FileDiff], repo_dir: str, contents: dict[str, str], applied: bool, *, added: bool = True) -> tuple[list[_Removed], Counter[str]]:
    """Every name the PR removes from a module, each with its uses and how
    it came out. `added`: whether a use on a line the PR adds counts."""
    tally: Counter[str] = Counter()
    modules = [f for f in files if f.status == "modified" and not f.binary and f.path.endswith(".py") and f.deletions
               and not any(part in SKIP_DIRS or part.startswith(".") for part in f.path.split("/")[:-1])]
    if not modules:
        return [], tally
    repo = _Repo(repo_dir, files, contents, applied)
    if not repo.complete:
        return [], Counter({"checkout too big": 1})
    removed: list[_Removed] = []
    for file in sorted(modules, key=lambda f: -f.deletions)[:MAX_CHECKED]:
        removed += _removed(repo, file, tally)
    if len(removed) > MAX_NAMES:
        tally["name: more removed than are looked for"] += len(removed) - MAX_NAMES
        removed = removed[:MAX_NAMES]
    wanted = {(item.module, item.name): item for item in removed if item.outcome != OPEN}
    if wanted:
        readers, mentioned, cut = _mentions(repo, set(wanted))
        names = {name for _, name in wanted}
        for path in readers:
            text = repo.text(path)
            if text is None:
                continue  # a file the PR changes in a way that can't be followed: it may have fixed the use
            lines = set(range(1, text.count("\n") + 2))
            if not added and path in repo.changed:
                lines -= {number for number, _ in repo.changed[path].added()}
            for use in _missing_on(repo, path, lines, names):
                if (use.module, use.name) in wanted:
                    wanted[(use.module, use.name)].uses.append(use)
        # Only now is the whole checkout read again: something else may set the name from outside.
        patched = repo.set_from_outside({key for key, item in wanted.items() if item.uses})
        for key, item in wanted.items():
            if item.uses:
                item.outcome = SET_ELSEWHERE if key in patched else REPORTED
                item.moved = _new_home(repo, item) if item.outcome == REPORTED else ""
            elif key in mentioned:
                item.outcome = UNREAD if cut else UNPROVEN  # a guard, an import that can't be pinned to one file, another module's name
    tally.update("name: " + item.outcome for item in removed)
    return removed, tally


# ── what the PR removes ──────────────────────────────────────────────────


def _removed(repo: _Repo, file: FileDiff, tally: Counter[str]) -> list[_Removed]:
    """The names one modified file's top level bound before the PR, on a
    line the diff removes, and binds nowhere after it."""
    texts = _texts(repo, file)
    before = _parse(texts[0]) if texts else None
    after = repo.module(file.path) if before is not None else None
    tally["module: " + ("read" if after is not None else "no text before and after the PR" if texts is None else "doesn't parse")] += 1
    if after is None:
        return []
    still = set(after.names)
    if texts[1] != repo.text(file.path):
        # The caller's text of the file isn't the checkout's with the diff applied: a name either has is still there.
        merged = _parse(texts[1])
        if merged is None:
            return []
        still |= _top_level(merged)[0]
    gone = {line.old for hunk in file.hunks for line in hunk.lines if line.kind == "-"}
    defined = _defined(before)
    new = set(after.names) - set(defined)
    found = []
    for name, places in sorted(defined.items()):
        lines = [(line, kind) for line, kind in sorted(places) if line in gone]
        if not lines or _dunder(name):
            continue
        if name in still:
            tally["name: its line is removed, and the module still defines it: nothing to report"] += 1
            continue
        found.append(_Removed(file.path, name, lines[0][1], lines[0][0], new, outcome=OPEN if after.open else UNUSED, why=after.open))
    return found


def _texts(repo: _Repo, file: FileDiff) -> tuple[str, str] | None:
    """A modified file's text before the PR, and that text with the diff
    applied. None when the diff doesn't lead from the one to the other: the
    lines it removes aren't lines of this file then."""
    new = repo.text(file.path)
    if new is None:
        return None
    old = _patched(new, _reversed(file)) if repo.applied else _read(repo.repo_dir, file.path)
    after = _patched(old, file) if old is not None else None
    return (old, after) if after is not None else None


def _reversed(file: FileDiff) -> FileDiff:
    """The diff read backwards, from the new text to the old."""
    swap = {"+": "-", "-": "+", " ": " "}
    return FileDiff(path=file.path, hunks=[
        Hunk(hunk.new_start, hunk.old_start, hunk.header, [Line(swap[line.kind], line.text, line.new, line.old) for line in hunk.lines])
        for hunk in file.hunks
    ])


def _defined(tree: ast.Module) -> dict[str, list[tuple[int, str]]]:
    """What a module's top level binds, whichever branch runs: each name
    with the line and the kind of every statement that binds it. Unlike
    `_top_level`, which errs towards a name being defined, this leaves out
    what may not be one: a comprehension's variable, a `global` in a
    function."""
    found: dict[str, list[tuple[int, str]]] = {}
    stack: list[ast.AST] = list(tree.body)
    while stack:
        node = stack.pop()
        if isinstance(node, (ast.FunctionDef, ast.AsyncFunctionDef, ast.ClassDef)):
            found.setdefault(node.name, []).append((node.lineno, "class" if isinstance(node, ast.ClassDef) else "function"))
            continue
        if isinstance(node, (ast.Lambda, ast.ListComp, ast.SetComp, ast.DictComp, ast.GeneratorExp)):
            continue
        if isinstance(node, ast.Import):
            for alias in node.names:
                found.setdefault(alias.asname or alias.name.partition(".")[0], []).append((alias.lineno, "import"))
        elif isinstance(node, ast.ImportFrom):
            for alias in node.names:
                if alias.name != "*":
                    found.setdefault(alias.asname or alias.name, []).append((alias.lineno, "import"))
        elif isinstance(node, ast.Name) and isinstance(node.ctx, ast.Store):
            found.setdefault(node.id, []).append((node.lineno, "name"))
        stack.extend(ast.iter_child_nodes(node))
    return found


def _new_home(repo: _Repo, item: _Removed) -> str:
    """Another Python file where the PR adds a definition of the name: the
    finding can then say where it seems to have moved. Only a hint, so only
    the first few files that could hold one are parsed."""
    start = re.compile(rf"^[ \t]*(?:(?:async[ \t]+)?def|class)[ \t]+{re.escape(item.name)}\b|^[ \t]*{re.escape(item.name)}[ \t]*[:=]", re.M)
    looked = 0
    for path, file in sorted(repo.changed.items()):
        text = repo.text(path) if path != item.module and path.endswith(".py") and looked < 5 else None
        if text is None or not start.search(text):
            continue
        looked += 1
        tree = _parse(text)
        added = {number for number, _ in file.added()}
        if tree is not None and any(line in added and kind != "import" for line, kind in _defined(tree).get(item.name, ())):
            return path
    return ""


# ── who still uses it ────────────────────────────────────────────────────


def _mentions(repo: _Repo, wanted: set[tuple[str, str]]) -> tuple[list[str], set[tuple[str, str]], bool]:
    """The files worth parsing for a use of these (module, name) pairs: the
    ones whose text has one of the names where an import or an attribute
    would have it, and a word an import of its module must have. Also the
    pairs some file mentions that way, and whether there were more such
    files than are read. Reads every Python file, so only once a PR has
    removed a name."""
    names = re.compile(r"(?<!\w)(?:" + "|".join(sorted({re.escape(name) for _, name in wanted})) + r")(?!\w)")
    # An import names the module by its last part: `http` for app/http.py, `app` for app/__init__.py.
    stems = {module: (module.rpartition("/")[0] if _is_init(module) else module[:-3]).rpartition("/")[2] for module, _ in wanted}
    by_name: dict[str, set[str]] = {}
    for module, name in wanted:
        by_name.setdefault(name, set()).add(module)
    imports = {module: re.compile(rf"(?<!\w){re.escape(stem)}(?!\w)" + (r"|from\s+\.+\s+import" if _is_init(module) else ""))
               for module, stem in stems.items() if stem}
    readers: list[str] = []
    mentioned: set[tuple[str, str]] = set()
    paths = sorted(repo.python)
    with ThreadPoolExecutor(max_workers=8) as pool:
        for at in range(0, len(paths), 256):
            batch = paths[at:at + 256]
            # Not all through repo.text, which would keep every file's text.
            for path, text in zip(batch, pool.map(lambda p: repo.text(p) if p in repo.changed else _read(repo.repo_dir, p), batch)):
                if text is None or _minified(text):
                    continue
                found: set[str] = set()
                for match in names.finditer(text):
                    if match.group() not in found and _could_use(text, match.start()):
                        found.add(match.group())
                here = {(module, name) for name in found for module in by_name[name] if module != path}
                named = {module for module in {module for module, _ in here} if module in imports and imports[module].search(text)}
                if named:
                    mentioned |= {(module, name) for module, name in here if module in named}
                    readers.append(path)
    return readers[:MAX_READ], mentioned, len(readers) > MAX_READ


def _could_use(text: str, start: int) -> bool:
    """Whether the name at `start` is where a use would have it: after a
    dot, on a line with `import`, or on a line of nothing but names (the
    rest of an import list), and not in a comment. A loop variable called
    `name` is removed now and then, and most files have that word."""
    first = text.rfind("\n", 0, start) + 1
    if "#" in text[first:start]:
        return False
    at = start - 1
    while at >= 0 and text[at] in " \t\r\n\\":
        at -= 1
    if at >= 0 and text[at] == ".":
        return True
    end = text.find("\n", start)
    line = text[first:end if end >= 0 else len(text)]
    return "import" in line or _NAMES_ONLY.fullmatch(line) is not None


# ── the finding ──────────────────────────────────────────────────────────


def _anchor(file: FileDiff, line: int) -> int | None:
    """A line the diff shows, next to the removed old line `line`: the first
    added or unchanged line after it, else the last one before it. None when
    its hunk shows no line of the new file."""
    for hunk in file.hunks:
        at = next((index for index, shown in enumerate(hunk.lines) if shown.kind == "-" and shown.old == line), None)
        if at is not None:
            after = next((shown.new for shown in hunk.lines[at:] if shown.new is not None), None)
            return after or next((shown.new for shown in reversed(hunk.lines[:at]) if shown.new is not None), None)
    return None


def _finding(item: _Removed, file: FileDiff) -> Finding:
    # A line that fails as soon as its file is imported comes before one that fails later.
    uses = sorted({(use.path, use.line): use for use in item.uses}.values(), key=lambda use: (use.kind != "import" or use.lazy, use.path, use.line))
    first = uses[0]
    # Then one line of each other file, before any file's second.
    seen = {first.path}
    others = [use for use in uses[1:] if use.path not in seen and not seen.add(use.path)]
    others += [use for use in uses[1:] if use not in others]
    name, module = item.name, f"`{item.module}`"
    removed = f"This PR removes {_KINDS[item.kind]} `{name}` from {module}, and nothing else there binds that name."
    where = f"`{first.path}:{first.line}`"
    if first.kind == "import":
        when = "when that function runs" if first.lazy else "as soon as that file is imported"
        fails = f" {where} still imports it, which raises ImportError {when}."
    else:
        fails = f" {where} still uses `{first.written}.{name}`, which raises AttributeError when that line runs."
    more = ""
    if others:
        listed = ", ".join(f"`{use.path}:{use.line}`" for use in others[:_SHOWN])
        more = (f" {count(len(others), 'other line')} still use{'s' if len(others) == 1 else ''} it: {listed}"
                + (f" and {len(others) - _SHOWN} more" if len(others) > _SHOWN else "") + ".")
    meant, _ = _nearest(name, item.added)
    those = "those lines" if others else "that line"
    if item.moved:
        hint = f" The PR adds a `{name}` to `{item.moved}`, so this looks like a move that missed {those}."
        suggestion = f"Import `{name}` from `{item.moved}` in {those}, or import it back into {module}."
    elif meant:
        hint = f" The PR adds `{meant}` there, so this looks like a rename that missed {those}."
        suggestion = f"Change {those} to use `{meant}`, or keep `{name}` in {module} as an alias."
    else:
        hint, suggestion = "", f"Update or remove {those}, or keep `{name}` in {module}."
    anchor = _anchor(file, item.line)
    return Finding(
        title=f"{module} no longer has `{name}`, which is still used",
        detail=removed + fails + more + hint,
        severity="high" if first.kind == "import" and not first.lazy else "medium",
        category="bug",
        path=item.module,
        line_start=anchor,
        line_end=anchor,
        suggestion=suggestion,
        source="check",
        rule=RULE,
        confidence=0.9,
    )
