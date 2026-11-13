"""A name its module doesn't define, in Python: `from app.utils import
normalize_path` when `app/utils.py` has no `normalize_path`, or
`os.path.makedirs(...)`. The name looks right, so a reader skims past it, and
it fails only when the line runs. Finding it needs no model, only the list of
names each module has, and every finding can be checked by opening one file.

On the lines a PR adds, two things are read:

- `from pkg.mod import name`, and `mod.name` where `mod` was bound by an
  import (`import pkg.mod`, `import pkg.mod as mod`, `from pkg import mod`),
  when `pkg.mod` is a file of the repository. The module is read as it will
  be after the PR: the PR's own text for the files it changes, the default
  branch's checkout for the rest.
- The same for the standard-library modules listed in `stdlib_names`.

Packages from outside the repository are not checked: their code isn't in the
checkout, and installing it to look is not something a review should do.

Nothing here imports or runs the repository's code: files are parsed with
`ast`. It never guesses either. A finding needs every one of these to hold,
and anything else is left alone:

- The import resolves to one file: a regular package (`__init__.py` all the
  way up) under the repository root, a `src/`, or a folder the importing
  file is in, with no second place the same top-level name could come from,
  and no compiled or Cython file beside it with its name. A module that
  isn't in the checkout is never reported: a build may write it. Namespace
  packages, `.pyi`-only modules and C extensions fall out here.
- That file's names can be listed. Code adds a name to its own module only
  through the module's dict or the module object, so a file that reaches
  for either is left alone: `globals()`, `exec` or `locals()` at the top
  level, `sys.modules` (other than to look up another module by name),
  `import_module(__name__)`, `__path__`, a `__getattr__` (unless it's the
  plain kind that answers for a few names spelled out in it). So is one
  with a star import that can't be followed to another file of the
  repository. A name bound in any branch (`if`, `try`, `TYPE_CHECKING`)
  counts as defined, and so does a package's submodule.
- A name missing from a package (`__init__.py`) is reported only in a
  repository with no native sources and no `.gitignore` rule that could cover
  it, since a build step may add the submodule.
- Nothing else in the repository sets the name from outside: no `x.name = …`
  anywhere, no `"name"` on a line with `setattr` or a patch, and the module
  itself isn't handed to `setattr` or `vars`.
- For `mod.name`: `mod` is bound only by that one import in the whole file
  (no variable, argument or second import of that name, no star import), and
  the file neither assigns `.name` nor has `"name"` as a string.
- Nothing catches the failure or expects it: the line isn't inside a `try`
  that catches, a `with suppress(…)` or `with pytest.raises(…)`, a test
  marked `xfail`, an `if hasattr(…)` or `if TYPE_CHECKING:` (or after an
  early return on one), or an annotation.

What that leaves out: an attribute of anything that isn't a module (a class,
an object, a name imported from a module), and a name set on a module by
code that got the module some other way.

The standard library differs between Python versions and platforms, and the
review may run on another Python than the project does. So the running
interpreter isn't trusted alone: `stdlib_names` holds, for a short list of
modules, every name each has had in CPython 3.8 to 3.15, read from CPython's
own sources with the rules above (both branches of every platform test
included). A name is reported only when it's in none of those versions and
the running interpreter doesn't have it either, which covers a runner newer
than the list. Modules whose names can't be listed from their source (`os`,
`socket`, `re`, anything written in C) aren't on the list and are never
reported. That still leaves a Python newer than both the list and the runner,
so three more things keep a standard-library name from being reported: the
project's own files naming such a Python (`requires-python`, classifiers,
`.python-version`, tox, CI workflows), the line being under a test of the
version or platform (`sys.version_info`, `os.name`, a constant in capitals,
a `skipif`), and the file asking for `sys.version_info` anywhere. A project
that holds a file or folder named like the module is left alone too, and so
is a name in capitals: constants are what a build may lack and what other
packages add to a module (`logging.TRACE`).

Without a checkout nothing is checked: there is no way to tell that a module
has one file only. The checkout is taken to be the default branch as it is
now, as the Action's and the App's are. One older than the PR's base could
lack a name the PR is right to use.
"""

from __future__ import annotations

import ast
import difflib
import fnmatch
import importlib
import importlib.util
import os
import re
import sys
from collections import Counter
from concurrent.futures import ThreadPoolExecutor
from dataclasses import dataclass, field

from stratum_open.diff import FileDiff
from stratum_open.findings import Finding
from stratum_open.policy import Policy
from stratum_open.repo.graph import MAX_BYTES, MAX_FILES, SKIP_DIRS, _minified, _read
from stratum_open.repo.stdlib_names import NAMES as STDLIB
from stratum_open.repo.stdlib_names import NEWEST as STDLIB_NEWEST

RULE = "code/unknown-name"
MAX_ENTRIES = 200_000  # files and folders walked in the checkout; past it, nothing is said
MAX_CHECKED = 50  # changed Python files read in one PR, the biggest changes first
MAX_PARSED = 400  # modules parsed for one PR
MAX_PR_FILES = 3000  # GitHub lists no more of a PR's files, so a bigger PR may add modules that can't be seen
_STAR_DEPTH = 8

# Beside a module, a file of the same name with one of these is what Python may load instead.
_NATIVE_TWINS = (".pyx", ".pxd", ".pyd", ".so", ".dll", ".dylib", ".c", ".cc", ".cpp", ".cxx", ".rs")
# Anywhere in the checkout, these mean a build step may add modules that aren't in it.
_NATIVE_SOURCES = _NATIVE_TWINS + (".h", ".hpp", ".f", ".f90", ".pyf", ".proto", ".i")
_NATIVE_FILES = frozenset({"CMakeLists.txt", "Cargo.toml", "meson.build"})
_PRESENCE_TESTS = frozenset({"hasattr", "getattr", "find_spec", "TYPE_CHECKING"})
_VERSION_TESTS = frozenset({"version_info", "hexversion", "platform", "implementation"})
_HANDLED, _VERSIONED = 2, 1  # how much a guard takes away: everything, or the standard library's answers
_SELF_IMPORTS = frozenset({"import_module", "__import__", "getmodule", "reload"})  # with `__name__`: the module itself
_INJECTORS = frozenset({"global_enum", "_convert_"})  # the enum module's ways of putting members into the caller's module
_SETTERS = re.compile(r"setattr|__dict__|globals|vars\(|patch|modules|update\(")
# Where a project says which Python it runs on.
_VERSION_FILES = (".python-version", "pyproject.toml", "setup.cfg", "setup.py", "tox.ini", "runtime.txt", "Dockerfile", "noxfile.py")
_PYTHON_3 = re.compile(r"(?<![\w.])3\.(\d{1,2})(?![\w]|\.\d+\.)|\bpy3(\d{1,2})\b")
_VERSION_ITEM = re.compile(r"""^\s*-?\s*\[?\s*["']?3\.\d{1,2}(?:-dev)?["']?[\s,\]"'\d.-]*$""")


def unknown_name_findings(files: list[FileDiff], policy: Policy, repo_dir: str | None, *, contents: dict[str, str] | None = None,
                          applied: bool = False) -> list[Finding]:
    """`repo_dir`: the default branch's checkout. `contents`: a changed
    file's full new text, where the caller has it; without it the text is
    the checkout's with the diff applied. `applied`: the checkout already
    holds the change (a local review of HEAD).

    Off unless `checks.unknown_names` is set. It never raises: an error
    becomes the low finding the other checks give when one of them fails."""
    if not policy.checks.unknown_names or not repo_dir or len(files) >= MAX_PR_FILES:
        return []
    try:
        return _check(files, repo_dir, contents or {}, applied)[0]
    except Exception as exc:  # the review goes on without this check
        return [_failed("unknown names", exc)]


def _failed(name: str, exc: Exception) -> Finding:
    """What a check that reads the repository says in place of its findings
    when it raised: the low finding `run_checks` gives for one of its own."""
    message = f"{type(exc).__name__}: {exc}"[:200]
    return Finding(
        title=f"The {name} check didn't run",
        detail=f"The {name} check failed with an error ({message}), so this review is missing its findings. The other checks ran.",
        severity="low",
        category="workflow",
        suggestion="Re-run the review. If it fails again, please report it with this message.",
        source="check",
        rule="checks/error",
        confidence=1.0,
    )


def _check(files: list[FileDiff], repo_dir: str, contents: dict[str, str], applied: bool) -> tuple[list[Finding], Counter[str]]:
    """The findings, and how each name looked up came out (for the benchmark)."""
    changed = [f for f in files if f.status != "deleted" and not f.binary and f.path.endswith(".py") and f.additions
               and not any(part in SKIP_DIRS or part.startswith(".") for part in f.path.split("/")[:-1])]
    if not changed:
        return [], Counter()
    repo = _Repo(repo_dir, files, contents, applied)
    if not repo.complete:
        return [], Counter({"checkout too big": 1})
    missing: list[_Missing] = []
    for file in sorted(changed, key=lambda f: -f.additions)[:MAX_CHECKED]:
        missing.extend(_file_missing(repo, file))
    return _report(repo, missing), repo.tally


def _report(repo: _Repo, missing: list[_Missing]) -> list[Finding]:
    """One finding for each name a file misses, however many lines use it."""
    if not missing:
        return []
    # Only now is the whole checkout read: something else may set the name from outside.
    patched = repo.set_from_outside({(m.module, m.name) for m in missing})
    repo.tally["set elsewhere"] += sum((m.module, m.name) in patched for m in missing)
    grouped: dict[tuple[str, str, str], list[_Missing]] = {}
    for m in missing:
        if (m.module, m.name) not in patched:
            grouped.setdefault((m.path, m.module, m.name), []).append(m)
    findings = [_finding(sorted(hits, key=lambda m: (m.kind != "import", m.line))) for hits in grouped.values()]
    return sorted(findings, key=lambda f: (f.path, f.line_start or 0))


# ── what a module defines ────────────────────────────────────────────────


@dataclass
class _Module:
    names: set[str]  # everything its top level may bind, in any branch
    open: str = ""  # why it may have names its text doesn't show; "" when it can't


def _parse(text: str) -> ast.Module | None:
    try:
        return ast.parse(text.removeprefix("\ufeff"))
    except (SyntaxError, ValueError, RecursionError, MemoryError):  # the last two: a huge generated expression
        return None


def _top_level(tree: ast.Module) -> tuple[set[str], set[str], list[tuple[int, str]]]:
    """What a module's top level may bind, whichever branch runs; the names it
    reads there; and its star imports, as (dots, module). Functions and
    classes are their own scopes, so only their names count."""
    bound: set[str] = set()
    read: set[str] = set()
    stars: list[tuple[int, str]] = []
    stack: list[ast.AST] = list(tree.body)
    while stack:
        node = stack.pop()
        if isinstance(node, (ast.FunctionDef, ast.AsyncFunctionDef, ast.ClassDef)):
            bound.add(node.name)
            continue
        if isinstance(node, ast.Lambda):
            continue
        if isinstance(node, ast.Import):
            bound.update(alias.asname or alias.name.partition(".")[0] for alias in node.names)
        elif isinstance(node, ast.ImportFrom):
            for alias in node.names:
                if alias.name == "*":
                    stars.append((node.level, node.module or ""))
                else:
                    bound.add(alias.asname or alias.name)
        elif isinstance(node, ast.Name):
            (read if isinstance(node.ctx, ast.Load) else bound).add(node.id)
        elif isinstance(node, (ast.ExceptHandler, ast.MatchAs, ast.MatchStar)) and node.name:
            bound.add(node.name)
        elif isinstance(node, ast.MatchMapping) and node.rest:
            bound.add(node.rest)
        stack.extend(ast.iter_child_nodes(node))
    # A function that declares a name global binds it in the module when it runs.
    bound.update(name for node in ast.walk(tree) if isinstance(node, ast.Global) for name in node.names)
    return bound, read, stars


def _dynamic(tree: ast.Module, bound: set[str], read: set[str]) -> str:
    """Why the module can have names its text doesn't show, or be swapped
    for another object; "" when it can't. Code adds a name to its own module
    through the module's dict or the module object, so every way to get hold
    of either counts, whatever is then done with it."""
    if "__path__" in bound or "__path__" in read:
        return "__path__"
    for name in ("locals", "vars", "exec"):  # at the top level, these are the module's dict
        if name in read:
            return f"{name}()"
    # Looking another module up by its name says nothing about this one:
    # `sys.modules.get("x")`, `sys.modules["x"]`, `"x" in sys.modules`.
    lookups: set[int] = set()
    for node in ast.walk(tree):
        if isinstance(node, ast.Call) and isinstance(node.func, ast.Attribute) and node.func.attr == "get" and node.args and _is_text(node.args[0]):
            lookups.add(id(node.func.value))
        elif isinstance(node, ast.Subscript) and isinstance(node.ctx, ast.Load) and _is_text(node.slice):
            lookups.add(id(node.value))
        elif isinstance(node, ast.Compare) and _is_text(node.left) and all(isinstance(op, (ast.In, ast.NotIn)) for op in node.ops):
            lookups.update(map(id, node.comparators))
    for node in ast.walk(tree):
        if isinstance(node, ast.Name) and node.id == "globals":
            return "globals()"
        if (node.id if isinstance(node, ast.Name) else node.attr if isinstance(node, ast.Attribute) else "") in _INJECTORS:
            return "an enum's members as module names"
        if isinstance(node, ast.Attribute) and node.attr == "modules" and isinstance(node.value, ast.Name) and node.value.id == "sys" and id(node) not in lookups:
            return "sys.modules"
        if isinstance(node, ast.ImportFrom) and node.module == "sys" and any(alias.name == "modules" for alias in node.names):
            return "sys.modules"
        if isinstance(node, ast.Call) and _callee(node) in _SELF_IMPORTS and _names_itself(node):
            return f"{_callee(node)}(__name__)"
    # A bare call handed the module's own name can replace the module (`apipkg.initpkg(__name__, …)`).
    for node in tree.body:
        if isinstance(node, ast.Expr) and isinstance(node.value, ast.Call) and _names_itself(node.value):
            return f"{_callee(node.value)}(__name__)"
    return ""


def _answered(tree: ast.Module) -> set[str] | None:
    """The names a module's `__getattr__` answers for, when it's the plain
    kind that keeps a deprecated name alive: `if name == "old": …`, and
    AttributeError for the rest. None when it could answer for anything: the
    argument must only be compared with text, or put in a message."""
    shims = [node for node in tree.body if isinstance(node, ast.FunctionDef) and node.name == "__getattr__"]
    rest = ast.Module(body=[node for node in tree.body if node not in shims], type_ignores=[])
    if len(shims) != 1 or "__getattr__" in _top_level(rest)[0]:
        return None
    [shim] = shims
    args = shim.args
    if shim.decorator_list or args.vararg or args.kwarg or args.kwonlyargs or len(args.posonlyargs + args.args) != 1:
        return None
    asked = (args.posonlyargs + args.args)[0].arg
    parents = {child: node for node in ast.walk(shim) for child in ast.iter_child_nodes(node)}
    names: set[str] = set()
    for node in ast.walk(shim):
        if not isinstance(node, ast.Name) or node.id != asked:
            continue
        if not isinstance(node.ctx, ast.Load):
            return None
        parent = parents[node]
        if isinstance(parent, ast.Compare) and all(isinstance(op, (ast.Eq, ast.NotEq, ast.In, ast.NotIn)) for op in parent.ops):
            texts = [_texts(side) for side in [parent.left] + parent.comparators if side is not node]
            if all(found is not None for found in texts):
                names.update(text for found in texts for text in found)
                continue
        if isinstance(parent, ast.Subscript) and parent.slice is node and isinstance(parent.value, ast.Dict) and all(
                key is not None and _is_text(key) for key in parent.value.keys):
            names.update(key.value for key in parent.value.keys)  # `{"old": …}[name]`
            continue
        above = parent
        while above is not shim and not (isinstance(above, (ast.JoinedStr, ast.Raise))
                                         or (isinstance(above, ast.Call) and _callee(above) in ("warn", "_deprecated"))):
            above = parents[above]
        if above is shim:
            return None
    return names


def _is_text(node: ast.AST) -> bool:
    return isinstance(node, ast.Constant) and isinstance(node.value, str)


def _texts(node: ast.AST) -> list[str] | None:
    """The strings of `"a"` or `("a", "b")`; None for anything else."""
    if _is_text(node):
        return [node.value]
    if isinstance(node, (ast.Tuple, ast.List, ast.Set)) and all(_is_text(item) for item in node.elts):
        return [item.value for item in node.elts]
    return None


def _callee(call: ast.Call) -> str:
    return call.func.id if isinstance(call.func, ast.Name) else call.func.attr if isinstance(call.func, ast.Attribute) else ""


def _names_itself(call: ast.Call) -> bool:
    return any(isinstance(node, ast.Name) and node.id == "__name__" for node in ast.walk(call))


def _dunder(name: str) -> bool:
    return name.startswith("__") and name.endswith("__")


# ── the repository, as it will be after the PR ───────────────────────────


class _Repo:
    """The checkout's files with the PR's changes on top: which file an
    import names, and what that file defines."""

    def __init__(self, repo_dir: str, files: list[FileDiff], contents: dict[str, str], applied: bool, *, stdlib: bool = False):
        self.repo_dir = repo_dir
        self.contents = contents
        self.applied = applied
        self.stdlib = stdlib  # reading CPython's own Lib/, where standard-library names are the files
        self.entries: dict[str, set[str]] = {"": set()}  # folder -> what's in it
        self.python: set[str] = set()
        self.native = False
        self.complete = self._walk()
        self.changed = {f.path: f for f in files if f.status != "deleted"}
        for file in files:
            if file.status == "deleted":
                self._remove(file.path)
            elif file.status == "renamed" and file.old_path and file.old_path != file.path:
                self._remove(file.old_path)
        for path in self.changed:
            self._add(path)
        # Every name a Python file, or a folder holding one, goes by: what an import could find.
        self.taken = {part.removesuffix(".py") for path in self.python for part in path.split("/")}
        self._modules: dict[str, _Module | None] = {}
        self._texts: dict[str, str | None] = {}
        self._parsed = 0
        self._cut = 0  # star imports not followed because they lead back to a module being read
        self._providers: dict[str, set[str]] | None = None
        self._ceiling: int | None = None
        self.tally: Counter[str] = Counter()  # how each name looked for came out, for the benchmark

    def _walk(self) -> bool:
        seen = 0
        for folder, dirs, names in os.walk(self.repo_dir):
            rel = os.path.relpath(folder, self.repo_dir).replace(os.sep, "/")
            rel = "" if rel == "." else rel
            self.entries.setdefault(rel, set()).update(dirs, names)
            # What's inside these is not the project's code, but their names still count as taken.
            dirs[:] = [d for d in dirs if d not in SKIP_DIRS and not d.startswith(".")]
            seen += len(dirs) + len(names)
            for name in names:
                if name.endswith(".py"):
                    self.python.add(f"{rel}/{name}" if rel else name)
                elif name.endswith(_NATIVE_SOURCES) or name in _NATIVE_FILES:
                    self.native = True
            if seen > MAX_ENTRIES or len(self.python) > MAX_FILES:
                return False
        return True

    def _add(self, path: str) -> None:
        if path.endswith(".py"):
            self.python.add(path)
        elif path.endswith(_NATIVE_SOURCES) or path.rpartition("/")[2] in _NATIVE_FILES:
            self.native = True
        parts = path.split("/")
        for depth in range(len(parts)):
            self.entries.setdefault("/".join(parts[:depth]), set()).add(parts[depth])

    def _remove(self, path: str) -> None:
        self.python.discard(path)
        folder, _, name = path.rpartition("/")
        self.entries.get(folder, set()).discard(name)

    # ── text ──

    def text(self, path: str) -> str | None:
        """A file's text after the PR; None when it can't be known."""
        if path not in self._texts:
            file = self.changed.get(path)
            if file is not None:
                self._texts[path] = self._new_text(file)
            else:
                self._texts[path] = _read(self.repo_dir, path) if path in self.python else None
        return self._texts[path]

    def _new_text(self, file: FileDiff) -> str | None:
        """A changed file's new text: the caller's, or the checkout's with
        the diff applied. Either must agree with every line the diff shows,
        or the text isn't the one the line numbers are for."""
        if file.binary:
            return None
        shown = file.new_side()
        given = self.contents.get(file.path)
        if given is not None and _agrees(given, shown):
            return given
        if self.applied:
            text = _read(self.repo_dir, file.path)
        else:
            old = "" if file.status == "added" else _read(self.repo_dir, file.old_path or file.path)
            text = _patched(old, file) if old is not None else None
        if text is None or len(text) > MAX_BYTES or not _agrees(text, shown):
            return None
        return text

    # ── modules ──

    def module(self, path: str, seen: tuple[str, ...] = ()) -> _Module | None:
        """What the file defines; None when it can't be read or parsed."""
        if path in self._modules:
            return self._modules[path]
        text = self.text(path)
        if text is None or self._parsed >= MAX_PARSED:
            return None
        self._parsed += 1
        tree = _parse(text)
        if tree is None:
            self._modules[path] = None
            return None
        bound, read, stars = _top_level(tree)
        why, cut = _dynamic(tree, bound, read), self._cut
        if "__getattr__" in bound:
            answered = _answered(tree)
            why = why or ("" if answered is not None else "__getattr__")
            bound |= answered or set()
        for level, name in stars:
            # A star import takes no more than the other module's names, so all of them is a safe list.
            target = self.resolve(path, level, name)
            if target and (target in seen or target == path):
                self._cut += 1  # a circle of star imports: the first module read gets every name in it
                continue
            inner = self.module(target, seen + (path,)) if target and len(seen) < _STAR_DEPTH else None
            if inner is None or inner.open:
                why = why or f"from {'.' * level}{name} import *"
            else:
                bound |= inner.names
                if _is_init(target):
                    bound |= self.siblings(target.rpartition("/")[0])
        found = _Module(bound, why)
        if not seen or cut == self._cut:  # further into a circle, the list may be short
            self._modules[path] = found
        return found

    def siblings(self, folder: str) -> set[str]:
        """Everything in a package's folder that could be its submodule,
        whatever kind of file it is."""
        return {entry.partition(".")[0] for entry in self.entries.get(folder, ())}

    def is_package(self, folder: str) -> bool:
        return "__init__.py" in self.entries.get(folder, ())

    # ── which file an import names ──

    def resolve(self, importer: str, level: int, module: str) -> str | None:
        """The one file `module` is, imported from `importer`. None when it's
        not in the repository, or when more than one file could answer."""
        parts = module.split(".") if module else []
        folder = importer.rpartition("/")[0]
        if level:
            # Each dot is a regular package: a namespace package can have parts somewhere else.
            for _ in range(level - 1):
                if not folder or not self.is_package(folder):
                    return None
                folder = folder.rpartition("/")[0]
            return self.file_in(folder, parts) if self.is_package(folder) else None
        if not parts or (parts[0] in sys.stdlib_module_names and not self.stdlib) or self.is_package(""):
            return None
        top = parts[0]
        roots = self.providers().get(top, set())
        if len(roots) != 1:
            return None
        [root] = roots
        if not self._reaches(importer, root):
            return None
        if folder != root and (top in self.entries.get(folder, ()) or f"{top}.py" in self.entries.get(folder, ())):
            return None  # run as a script, the importer would find its own neighbour first
        return self.file_in(root, parts)

    def file_in(self, folder: str, parts: list[str]) -> str | None:
        """`a.b.c` under `folder`: every step a regular package, the last a
        module or a package, and nothing else there with its name."""
        if not parts:
            return _join(folder, "__init__.py") if self.is_package(folder) else None
        for part in parts[:-1]:
            folder = _join(folder, part)
            if not self.is_package(folder) or f"{part}.py" in self.entries.get(folder.rpartition("/")[0], ()):
                return None
        last = parts[-1]
        here = self.entries.get(folder, ())
        twins = [entry for entry in here if entry.partition(".")[0] == last and entry.endswith(_NATIVE_TWINS)]
        module, package = f"{last}.py" in here, self.is_package(_join(folder, last))
        if twins or module == package:
            return None
        if module:
            return _join(folder, f"{last}.py")
        return _join(folder, last, "__init__.py")

    def providers(self) -> dict[str, set[str]]:
        """Top-level import name -> the folders it could be imported from. A
        file's full name comes from walking up while there's an
        `__init__.py`. (A folder without one is at most a namespace package,
        which Python passes over when a regular package or a module of that
        name is anywhere on its path.)"""
        if self._providers is None:
            found: dict[str, set[str]] = {}
            for path in self.python:
                parts = path.split("/")
                folder, name = parts[:-1], parts[-1][:-3]
                while folder and self.is_package("/".join(folder)):
                    name = folder.pop()
                if name != "__init__":
                    found.setdefault(name, set()).add("/".join(folder))
            self._providers = found
        return self._providers

    @staticmethod
    def _reaches(importer: str, root: str) -> bool:
        """Whether code at `importer` plausibly has `root` on its path: the
        repository root, a folder it's inside, or a `src/` beside one."""
        if not root or importer.startswith(f"{root}/"):
            return True
        parent, _, name = root.rpartition("/")
        return name == "src" and (not parent or importer.startswith(f"{parent}/"))

    # ── what else could make a name exist ──

    def may_be_built(self, folder: str, name: str) -> bool:
        """Whether a build step could put a submodule `name` in this package:
        native sources anywhere, or an ignore rule that could cover it."""
        if self.native:
            return True
        parts = folder.split("/") if folder else []
        for depth in range(len(parts) + 1):
            text = _read(self.repo_dir, _join("/".join(parts[:depth]), ".gitignore")) or ""
            for line in text.splitlines():
                rule = line.strip().rstrip("/").rpartition("/")[2]
                if rule and not line.lstrip().startswith(("#", "!")) and (fnmatch.fnmatchcase(f"{name}.py", rule) or fnmatch.fnmatchcase(name, rule)):
                    return True
        return False

    def set_from_outside(self, wanted: set[tuple[str, str]]) -> set[tuple[str, str]]:
        """Of these (module, name) pairs, the ones something in the
        repository may set from outside the module: `x.name = …` anywhere,
        `"name"` on a line that sets attributes by name, or the module handed
        to `setattr` or `vars` or read through `__dict__`, going by the last
        part of its name. Reads every Python file, so only once there's
        something to report."""
        if not wanted:
            return set()
        names = "|".join(sorted({re.escape(name) for _, name in wanted}))
        called = {module: re.sub(r"(/__init__)?\.py$", "", module).replace("/", ".").rpartition(".")[2] for module, _ in wanted}
        modules = "|".join(sorted({re.escape(name) for name in called.values()}))
        assigned = re.compile(rf"\.\s*({names})\s*(?::[^=\n]+)?=(?!=)")
        quoted = re.compile(rf"""["']({names})["']""")
        handled = re.compile(rf"(?:setattr|delattr|vars)\(\s*(?:[\w.]+\.)?({modules})\s*[,)]|\b({modules})\.__dict__")
        found: set[str] = set()
        written: set[str] = set()
        paths = sorted(self.python)
        with ThreadPoolExecutor(max_workers=8) as pool:
            for at in range(0, len(paths), 256):
                # Not all through self.text, which would keep every file's text.
                for text in pool.map(lambda p: self.text(p) if p in self.changed else _read(self.repo_dir, p), paths[at:at + 256]):
                    if text is None or _minified(text):
                        continue
                    found.update(match.group(1) for match in assigned.finditer(text))
                    written.update(match.group(1) or match.group(2) for match in handled.finditer(text))
                    for match in quoted.finditer(text):
                        start = text.rfind("\n", 0, match.start()) + 1
                        end = text.find("\n", match.end())
                        if _SETTERS.search(text, start, end if end >= 0 else len(text)):
                            found.add(match.group(1))
        return {(module, name) for module, name in wanted if name in found or called[module] in written}

    def stdlib_usable(self, top: str) -> bool:
        """Whether `top` here can only be the standard library's module, on a
        Python the names are known for."""
        if top in self.taken:
            return False  # a file or folder of that name could be what's imported
        if self._ceiling is None:
            self._ceiling = self._newest_python()
        return self._ceiling <= max(STDLIB_NEWEST, sys.version_info[1])

    def _newest_python(self) -> int:
        """The newest Python 3 minor version the project's own files name."""
        paths = [name for name in _VERSION_FILES if name in self.entries[""]]
        try:
            workflows = sorted(os.listdir(os.path.join(self.repo_dir, ".github", "workflows")))
        except OSError:
            workflows = []
        paths += [f".github/workflows/{name}" for name in workflows if name.endswith((".yml", ".yaml"))][:30]
        newest = 0
        for path in paths:
            text = _read(self.repo_dir, path) or ""
            for line in text.splitlines():
                if path != ".python-version" and "python" not in line.lower() and "py3" not in line and not _VERSION_ITEM.match(line):
                    continue
                newest = max([newest] + [int(a or b) for a, b in _PYTHON_3.findall(line)])
        return newest


def _join(*parts: str) -> str:
    return "/".join(part for part in parts if part)


def _is_init(path: str) -> bool:
    return path.rpartition("/")[2] == "__init__.py"


def _agrees(text: str, shown: dict[int, str]) -> bool:
    lines = text.split("\n")
    return all(0 < number <= len(lines) and lines[number - 1].rstrip("\r") == line for number, line in shown.items())


def _patched(old: str, file: FileDiff) -> str | None:
    """`old` with the file's hunks applied; None when a hunk's unchanged or
    removed lines aren't what `old` has there (the checkout has moved on)."""
    source = old.split("\n")
    out: list[str] = []
    at = 0  # lines of `source` used so far
    for hunk in file.hunks:
        start = max(hunk.old_start - 1, 0)
        if start < at or start > len(source):
            return None
        out.extend(source[at:start])
        at = start
        for line in hunk.lines:
            if line.kind == "+":
                out.append(line.text)
                continue
            if at >= len(source) or source[at].rstrip("\r") != line.text:
                return None
            if line.kind == " ":
                out.append(source[at])
            at += 1
    return "\n".join(out + source[at:])


# ── one changed file ─────────────────────────────────────────────────────


@dataclass
class _Missing:
    path: str  # the file the line is in
    line: int
    kind: str  # "import" | "attribute"
    lazy: bool  # inside a function: it fails when that runs, not when the file is imported
    module: str  # the module's file, or its dotted name for the standard library
    stdlib: bool
    name: str
    written: str  # the module as the line names it
    names: set[str] = field(default_factory=set)  # what the module does define


def _file_missing(repo: _Repo, file: FileDiff) -> list[_Missing]:
    """The names one changed file imports or uses on its added lines that
    are certain not to be there."""
    return _missing_on(repo, file.path, {number for number, _ in file.added()})


def _missing_on(repo: _Repo, path: str, added: set[int], only: set[str] | None = None) -> list[_Missing]:
    """The same for any file of the repository and any set of its lines.
    With `only`, just those names are looked up: removed_names.py reads
    whole files this way, for the few names a PR removed."""
    text = repo.text(path)
    tree = _parse(text) if text is not None else None
    repo.tally["file: " + ("read" if tree is not None else "doesn't parse" if text is not None else "no text")] += 1
    if tree is None:
        return []
    imports, chains = _uses(tree, added)
    if only is not None:
        imports = [use for use in imports if use[1].name in only]
        chains = [use for use in chains if any(attr in only for attr, _ in use[1])]
    if not imports and not chains:
        return []
    found: list[_Missing] = []
    # A file that asks for the Python version anywhere may run its newer names only where they exist.
    versioned_file = any((node.attr if isinstance(node, ast.Attribute) else node.id if isinstance(node, ast.Name) else "") in ("version_info", "hexversion")
                         for node in ast.walk(tree))
    for node, alias, lazy, versioned in imports:
        module = node.module or ""
        if not node.level and module.partition(".")[0] in sys.stdlib_module_names:
            where = ("stdlib", module) if repo.stdlib_usable(module.partition(".")[0]) else None
        else:
            target = repo.resolve(path, node.level, module)
            where = ("repo", target) if target and target != path else None
        answer, _, (kind, target), names = _lookup(repo, where, [(alias.name, alias.lineno)]) if where else ("outside", 0, ("", ""), set())
        if answer == "missing" and kind == "stdlib" and (versioned or versioned_file):
            answer = "unknown"
        repo.tally[_outcome("import", kind, answer)] += 1
        if answer == "missing":
            found.append(_Missing(path, alias.lineno, "import", lazy, target, kind == "stdlib", alias.name, "." * node.level + module, names))
    if chains:
        bound, other, star = _bindings(tree)
        _, read, _ = _top_level(tree)
        own_dynamic = star or bool(_dynamic(tree, set(), read - {"__path__"}))
        stored = {node.attr for node in ast.walk(tree) if isinstance(node, ast.Attribute) and not isinstance(node.ctx, ast.Load)}
        strings = {node.value for node in ast.walk(tree) if isinstance(node, ast.Constant) and isinstance(node.value, str)}
        roots: dict[str, tuple[str, str] | None] = {}
        for root, attrs, lazy, versioned in chains:
            if root in other or len(bound.get(root, ())) != 1:
                continue  # not a module, as far as the file says
            if root not in roots:
                roots[root] = _module_of(repo, path, next(iter(bound[root])))
            where = roots[root]
            if where is None or where[1] == path or own_dynamic:
                repo.tally[_outcome("attribute", where[0] if where else "", "unknown" if where else "outside")] += 1
                continue
            answer, index, (kind, target), names = _lookup(repo, where, attrs)
            name, line = attrs[index]
            if line not in added:
                continue  # the part the answer is about isn't this PR's
            if answer == "missing" and (name in stored or name in strings or (kind == "stdlib" and (versioned or versioned_file))):
                answer = "unknown"
            repo.tally[_outcome("attribute", kind, answer)] += 1
            if answer == "missing":
                written = ".".join([root] + [attr for attr, _ in attrs[:index]])
                found.append(_Missing(path, line, "attribute", lazy, target, kind == "stdlib", name, written, names))
    return found


def _outcome(what: str, kind: str, answer: str) -> str:
    """A line of the benchmark's tally."""
    if answer == "outside" or not kind:
        return f"{what}: outside what is read"
    return f"{what} of {'the standard library' if kind == 'stdlib' else 'a module of the repository'}: {answer}"


def _uses(tree: ast.Module, added: set[int]) -> tuple[list[tuple[ast.ImportFrom, ast.alias, bool, bool]],
                                                       list[tuple[str, list[tuple[str, int]], bool, bool]]]:
    """The `from … import name`s on added lines, and the `name.a.b` chains
    with a part on one, where nothing would catch a failure. Each comes with
    whether it's inside a function, and whether it runs only on some Python
    version or platform (the standard library's answer isn't taken there)."""
    imports: list[tuple[ast.ImportFrom, ast.alias, bool, bool]] = []
    chains: list[tuple[str, list[tuple[str, int]], bool, bool]] = []

    def block(nodes: list[ast.stmt], guard: int, lazy: bool) -> None:
        for node in nodes:
            visit(node, guard, lazy)
            if isinstance(node, ast.If) and (_leaves(node.body) or _leaves(node.orelse)):
                guard = max(guard, _feature_test(node.test))  # after `if not hasattr(…): return`, the rest runs only where the test passed

    def visit(node: ast.AST, guard: int, lazy: bool) -> None:
        if isinstance(node, (ast.Try, ast.TryStar)):
            block(node.body, _HANDLED if node.handlers else guard, lazy)
            for handler in node.handlers:
                block(handler.body, guard, lazy)
            block(node.orelse, guard, lazy)
            block(node.finalbody, guard, lazy)
            return
        if isinstance(node, (ast.If, ast.While, ast.IfExp)):
            guard = max(guard, _feature_test(node.test))
        elif isinstance(node, ast.BoolOp):
            guard = max(guard, _feature_test(node))
        elif isinstance(node, (ast.With, ast.AsyncWith)) and any(_expects_failure(item.context_expr) for item in node.items):
            for item in node.items:  # the exception it names is a name like any other; the body is what may fail
                visit(item.context_expr, guard, lazy)
            return
        elif isinstance(node, ast.Call) and _expects_failure(node.func):
            for child in [node.func] + node.args + [keyword.value for keyword in node.keywords]:
                if not isinstance(child, ast.Lambda):  # `assertRaises(AttributeError, lambda: mod.name)`
                    visit(child, guard, lazy)
            return
        elif isinstance(node, (ast.FunctionDef, ast.AsyncFunctionDef, ast.ClassDef)):
            # `@skipif(sys.version_info < …)`, `@xfail`
            guard = max([guard] + [_HANDLED if _expects_failure(decorator) else _feature_test(decorator) for decorator in node.decorator_list])
        if guard == _HANDLED:
            return
        if isinstance(node, (ast.FunctionDef, ast.AsyncFunctionDef, ast.Lambda)):
            # Annotations aren't run for their value, so they're not read.
            for child in node.args.defaults + [d for d in node.args.kw_defaults if d is not None] + getattr(node, "decorator_list", []):
                visit(child, guard, lazy)
            if isinstance(node, ast.Lambda):
                visit(node.body, guard, True)
            else:
                block(node.body, guard, True)
            return
        if isinstance(node, ast.AnnAssign):
            visit(node.target, guard, lazy)
            if node.value is not None:
                visit(node.value, guard, lazy)
            return
        if isinstance(node, ast.ImportFrom):
            imports.extend((node, alias, lazy, guard == _VERSIONED) for alias in node.names if alias.name != "*" and alias.lineno in added)
            return
        if isinstance(node, ast.Attribute):
            attrs: list[tuple[str, int]] = []
            inner: ast.AST = node
            while isinstance(inner, ast.Attribute):
                if isinstance(inner.ctx, ast.Load):
                    attrs.append((inner.attr, inner.end_lineno or inner.lineno))
                inner = inner.value
            if not isinstance(inner, ast.Name):
                visit(inner, guard, lazy)
            elif attrs and any(line in added for _, line in attrs):
                chains.append((inner.id, attrs[::-1], lazy, guard == _VERSIONED))
            return
        for _, value in ast.iter_fields(node):
            if isinstance(value, list) and value and isinstance(value[0], ast.stmt):
                block(value, guard, lazy)
            elif isinstance(value, list):
                for child in value:
                    if isinstance(child, ast.AST):
                        visit(child, guard, lazy)
            elif isinstance(value, ast.AST):
                visit(value, guard, lazy)

    block(tree.body, 0, False)
    return imports, chains


def _feature_test(test: ast.AST) -> int:
    """What an expression asks before the code under it runs: _HANDLED when
    it's whether a name is there (`hasattr`, `TYPE_CHECKING`), so a missing
    one is expected; _VERSIONED when it's which Python, platform or build
    this is (`sys.version_info`, `os.name`, a constant in capitals); else 0."""
    level = 0
    for node in ast.walk(test):
        name = node.id if isinstance(node, ast.Name) else node.attr if isinstance(node, ast.Attribute) else ""
        if name in _PRESENCE_TESTS:
            return _HANDLED
        if name in _VERSION_TESTS or (len(name) > 1 and name.isupper()):
            level = _VERSIONED
        elif name == "name" and isinstance(node, ast.Attribute) and isinstance(node.value, ast.Name) and node.value.id == "os":
            level = _VERSIONED
    return level


def _expects_failure(expression: ast.AST) -> bool:
    """`pytest.raises(…)`, `assertRaises`, `suppress(…)`, `xfail`: what runs
    under it is meant to fail, or allowed to."""
    names = [node.id if isinstance(node, ast.Name) else node.attr for node in ast.walk(expression) if isinstance(node, (ast.Name, ast.Attribute))]
    return any(word in name.lower() for name in names for word in ("raises", "suppress", "xfail"))


def _leaves(body: list[ast.stmt]) -> bool:
    """Whether a block ends by leaving: `return`, `raise`, `continue`,
    `break`, or a call that exits or skips."""
    last = body[-1] if body else None
    if isinstance(last, (ast.Return, ast.Raise, ast.Continue, ast.Break)):
        return True
    return isinstance(last, ast.Expr) and isinstance(last.value, ast.Call) and any(word in _callee(last.value).lower() for word in ("exit", "skip", "abort"))


def _bindings(tree: ast.Module) -> tuple[dict[str, set[tuple]], set[str], bool]:
    """For every name in the file, in any scope: the imports that bind it,
    the names bound any other way, and whether there's a star import. A name
    is a module for certain only when one import, and nothing else, binds it."""
    imports: dict[str, set[tuple]] = {}
    other: set[str] = set()
    star = False
    for node in ast.walk(tree):
        if isinstance(node, ast.Import):
            for alias in node.names:
                top = alias.name.partition(".")[0]
                imports.setdefault(alias.asname or top, set()).add(("module", 0, alias.name if alias.asname else top))
        elif isinstance(node, ast.ImportFrom):
            for alias in node.names:
                if alias.name == "*":
                    star = True
                else:
                    imports.setdefault(alias.asname or alias.name, set()).add(("from", node.level, node.module or "", alias.name))
        elif isinstance(node, ast.Name) and not isinstance(node.ctx, ast.Load):
            other.add(node.id)
        elif isinstance(node, ast.arg):
            other.add(node.arg)
        elif isinstance(node, (ast.FunctionDef, ast.AsyncFunctionDef, ast.ClassDef)):
            other.add(node.name)
        elif isinstance(node, (ast.ExceptHandler, ast.MatchAs, ast.MatchStar)) and node.name:
            other.add(node.name)
        elif isinstance(node, ast.MatchMapping) and node.rest:
            other.add(node.rest)
        elif isinstance(node, (ast.Global, ast.Nonlocal)):
            other.update(node.names)
        elif isinstance(node, ast.If) and _feature_test(node.test) == _HANDLED:
            # An import under TYPE_CHECKING never ran.
            for inner in ast.walk(node):
                if isinstance(inner, (ast.Import, ast.ImportFrom)):
                    other.update(alias.asname or alias.name.partition(".")[0] for alias in inner.names)
    return imports, other, star


def _module_of(repo: _Repo, importer: str, binding: tuple) -> tuple[str, str] | None:
    """The module a name bound by this import is: ("repo", file) or
    ("stdlib", dotted name). None when it isn't certain to be one."""
    if binding[0] == "module":
        dotted = binding[2]
        top = dotted.partition(".")[0]
        if top in sys.stdlib_module_names:
            return ("stdlib", dotted) if repo.stdlib_usable(top) else None
        target = repo.resolve(importer, 0, dotted)
        return ("repo", target) if target else None
    _, level, module, name = binding
    if not level and module.partition(".")[0] in sys.stdlib_module_names:
        dotted = f"{module}.{name}"
        return ("stdlib", dotted) if dotted in STDLIB and repo.stdlib_usable(module.partition(".")[0]) and _is_submodule(module, name) else None
    # `from pkg import mod` is the submodule only when the package's own text doesn't bind that name to something else.
    package = repo.resolve(importer, level, module)
    summary = repo.module(package) if package and _is_init(package) else None
    if summary is None or summary.open or name in summary.names:
        return None
    target = repo.file_in(package.rpartition("/")[0], [name])
    return ("repo", target) if target else None


def _lookup(repo: _Repo, where: tuple[str, str], attrs: list[tuple[str, int]]) -> tuple[str, int, tuple[str, str], set[str]]:
    """Follow `module.a.b.c` from module to submodule until a part settles
    it. Returns (answer, which part, the module it was looked for in, that
    module's names). The answer is "missing" only when that's certain;
    otherwise "defined", "unknown" (the module's names can't be listed) or
    "outside" (not a module that's read)."""
    index = 0
    for index, (attr, _) in enumerate(attrs):
        if _dunder(attr):
            return "defined", index, where, set()
        kind, module = where
        if kind == "stdlib":
            child = f"{module}.{attr}"
            if child in STDLIB and _is_submodule(module, attr):
                where = ("stdlib", child)
                continue
            if module not in STDLIB:
                return "outside", index, where, set()
            if attr in STDLIB[module] or _running_has(module, attr):
                return "defined", index, where, set()
            if attr.isupper():
                return "unknown", index, where, set()  # a constant: a build may lack it, another package may add it
            return "missing", index, where, set(STDLIB[module])
        summary = repo.module(module)
        if summary is None or summary.open:
            return "unknown", index, where, set()
        if attr in summary.names:
            return "defined", index, where, set()
        if _is_init(module):
            folder = module.rpartition("/")[0]
            inside = repo.file_in(folder, [attr])
            if inside:
                where = ("repo", inside)
                continue
            if attr in repo.siblings(folder):
                return "defined", index, where, set()
            if repo.may_be_built(folder, attr):
                return "unknown", index, where, set()
        return "missing", index, where, summary.names | (repo.siblings(module.rpartition("/")[0]) if _is_init(module) else set())
    return "defined", index, where, set()


def _running_has(module: str, name: str) -> bool:
    """Whether this interpreter's `module` has `name`, as an attribute or a
    submodule. Only asked of the listed modules, which are safe to import;
    when it can't be asked, the answer is yes."""
    try:
        if hasattr(importlib.import_module(module), name):
            return True
        return importlib.util.find_spec(f"{module}.{name}") is not None
    except ModuleNotFoundError as exc:
        return exc.name != f"{module}.{name}"  # the module itself isn't in this Python: can't ask
    except Exception:
        return True


def _is_submodule(module: str, name: str) -> bool:
    """Whether `module.name` here is the submodule itself, not another
    object of that name."""
    try:
        child = importlib.import_module(f"{module}.{name}")
        return getattr(sys.modules.get(module), name, None) is child
    except Exception:
        return False


# ── the finding ──────────────────────────────────────────────────────────


def _nearest(name: str, names: set[str]) -> tuple[str, list[str]]:
    """The one name clearly meant, or else the few closest."""
    pool = sorted(n for n in names if not _dunder(n) and n != name and (name.startswith("_") or not n.startswith("_")))

    def flat(text: str) -> str:
        return text.replace("_", "").lower()

    same = [n for n in pool if flat(n) == flat(name)]
    if len(same) == 1:
        return same[0], []
    close = difflib.get_close_matches(name, pool, n=3, cutoff=0.6)
    scores = [difflib.SequenceMatcher(None, name, n).ratio() for n in close]
    if close and scores[0] >= 0.8 and (len(close) == 1 or scores[0] - scores[1] >= 0.1):
        return close[0], []
    return "", close


def _finding(hits: list[_Missing]) -> Finding:
    first = hits[0]
    name = first.name
    what = f"`{first.module}`"
    if first.stdlib:
        versions = f"3.8 to 3.{STDLIB_NEWEST}"
        defined = f"The standard library's {what} has no `{name}`: not in Python {versions}, and not in the Python this review ran on."
    else:
        defined = f"`{first.written}` is {what}, which defines no top-level `{name}`."
    if first.kind == "import":
        when = "when this function runs" if first.lazy else "as soon as this file is imported"
        fails = f" This line imports it, so it raises ImportError {when}."
    else:
        fails = f" `{first.written}.{name}` raises AttributeError when this line runs."
    meant, close = _nearest(name, first.names)
    parent = sys.modules.get(first.module.rpartition(".")[0]) if first.stdlib and "." in first.module else None
    if meant:
        hint = f" Did you mean `{meant}`?"
    elif parent is not None and hasattr(parent, name):
        hint = f" `{first.module.rpartition('.')[0]}.{name}` does exist."
    elif close:
        hint = " The closest names it has: " + ", ".join(f"`{n}`" for n in close) + "."
    else:
        hint = ""
    others = sorted({hit.line for hit in hits} - {first.line})
    also = (f" Lines {', '.join(map(str, others))} use it too." if len(others) > 1 else f" Line {others[0]} uses it too." if others else "")
    return Finding(
        title=f"{what} has no `{name}`",
        detail=defined + fails + hint + also,
        severity="high" if first.kind == "import" and not first.lazy else "medium",
        category="bug",
        path=first.path,
        line_start=first.line,
        line_end=first.line,
        suggestion=(f"Use `{meant}`." if meant else f"Use a name {what} has." if first.stdlib
                    else f"Use a name {what} defines, or add `{name}` to it in this PR."),
        source="check",
        rule=RULE,
        confidence=0.9,
    )
