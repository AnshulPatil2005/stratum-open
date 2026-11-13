"""Where the Python functions a PR changes are called, for the model to read
beside the diff (`review.context_callers`). A change to what a function
returns or takes can be wrong only for its callers, and they are rarely in
the diff.

For each top-level function, and each method of a top-level class, that has
a line the PR adds or removes, this lists up to N lines elsewhere in the
repository that call it, each with a few lines of code around it. A call is
listed only when it's certain to be that function, by the same reading of
imports as unknown_names.py, so nothing is guessed from a name alone:

- In the function's own module: `name(...)`, where nothing else in the
  file binds that name (no argument, variable, import or second `def`).
- In another module: `alias(...)`, where `alias` is bound only by one
  `from mod import name [as alias]` and that import resolves to the
  function, through re-exports (`from .impl import name` in a package's
  `__init__.py`) too; and `mod.name(...)`, where `mod` is bound only by an
  import of that module.
- For a method `C.m`: `C.m(...)` and, for `__init__`, `C(...)`, with `C`
  found as above; and `self.m(...)` or `cls.m(...)` inside `C`'s own
  methods, where `self` is the method's first argument and nothing rebinds
  it.

What that leaves out: a method called on an object (`client.send(...)`,
anything whose type would have to be inferred), a function passed as a
value and called later, a call from outside the repository, and modules the
import resolution can't pin to one file. Those are left out, not guessed.

The repository is read as it will be after the PR: the PR's own text for the
files it changes, the default branch's checkout for the rest. Calls on lines
the diff already shows are left out, and so are calls inside the changed
function itself. Nothing in the repository is imported or run.
"""

from __future__ import annotations

import ast
import re
from concurrent.futures import ThreadPoolExecutor
from dataclasses import dataclass, field

from stratum_open.diff import FileDiff
from stratum_open.policy import Policy
from stratum_open.repo.graph import SKIP_DIRS, _minified, _read
from stratum_open.repo.unknown_names import MAX_PR_FILES, _bindings, _is_init, _module_of, _parse, _Repo

MAX_FUNCTIONS = 20  # changed functions looked up in one PR, the most changed first
MAX_READ = 400  # files that mention one of them and are parsed for calls
MAX_DEPTH = 4  # re-exports followed from an import to the `def`
LINES_BEFORE = 2  # lines shown above a call
MAX_SITE_LINES = 6  # lines shown for one call, its own included
MAX_LINE_CHARS = 160
TAIL_TOKENS = 14  # of a block's room, kept for the line saying how many calls weren't shown
_CALL_DECORATORS = frozenset({"property", "cached_property", "setter", "getter", "deleter"})  # read, not called


@dataclass
class Site:
    path: str
    line: int
    within: str  # the function the call is in, or "" at a module's top level
    code: str  # the lines around the call, numbered


@dataclass
class Callers:
    path: str  # the file the PR changes
    name: str  # `parse`, `Cookie.parse`, or `Cookie` for its `__init__`
    line: int  # its `def`, in the new file
    sites: list[Site] = field(default_factory=list)  # the first N
    more: int = 0  # calls found past those


@dataclass
class _Target:
    path: str
    name: str  # the top-level name: the function, or the class of a method
    method: str  # "" for a function
    start: int
    end: int
    changed: int  # lines the PR adds or removes in it
    self_names: dict[int, str] = field(default_factory=dict)  # the line of each of its class's methods -> their first argument


def find_callers(files: list[FileDiff], policy: Policy, repo_dir: str | None, *, contents: dict[str, str] | None = None,
                 applied: bool = False) -> list[Callers]:
    """`repo_dir`: the default branch's checkout. `contents`: a changed
    file's full new text, where the caller has it. `applied`: the checkout
    already holds the change. Off unless `review.context_callers` is above
    0; without a checkout there is nothing to read. It never raises: the
    review goes on without callers."""
    limit = policy.review.context_callers
    if limit <= 0 or not repo_dir or len(files) >= MAX_PR_FILES:
        return []
    try:
        return _find(files, repo_dir, contents or {}, applied, limit)
    except Exception:  # extra context; a review never fails on it
        return []


def _find(files: list[FileDiff], repo_dir: str, contents: dict[str, str], applied: bool, limit: int) -> list[Callers]:
    changed = [f for f in files if f.status != "deleted" and not f.binary and f.path.endswith(".py") and f.hunks
               and not any(part in SKIP_DIRS or part.startswith(".") for part in f.path.split("/")[:-1])]
    if not changed:
        return []
    repo = _Repo(repo_dir, files, contents, applied)
    if not repo.complete:
        return []
    targets: list[_Target] = []
    for file in changed:
        text = repo.text(file.path)
        tree = _parse(text) if text is not None else None
        if tree is not None:
            targets += _changed(tree, file)
    targets = sorted(targets, key=lambda t: (-t.changed, t.path, t.start))[:MAX_FUNCTIONS]
    if not targets:
        return []
    shown = {f.path: set(f.new_side()) for f in files}
    found: dict[int, list[Site]] = {id(t): [] for t in targets}
    reader = _Reader(repo)
    for path in _mentions(repo, targets):
        tree = reader.tree(path)
        if tree is None:
            continue
        lines = (repo.text(path) or "").split("\n")
        for target, call in _calls(reader, path, tree, targets):
            if call.lineno in shown.get(path, ()) or (path == target.path and target.start <= call.lineno <= target.end):
                continue
            found[id(target)].append(Site(path, call.lineno, _within(tree, call.lineno), _numbered(lines, call)))
    result = []
    for target in targets:
        sites = sorted({(s.path, s.line): s for s in found[id(target)]}.values(), key=lambda s: (_is_test(s.path), s.path, s.line))
        if sites:
            name = target.name if target.method == "__init__" else f"{target.name}.{target.method}" if target.method else target.name
            result.append(Callers(target.path, name, target.start, sites[:limit], max(0, len(sites) - limit)))
    return result


# ── what the PR changes ──────────────────────────────────────────────────


def _changed(tree: ast.Module, file: FileDiff) -> list[_Target]:
    """The top-level functions, and the methods of top-level classes, with a
    line the PR adds or removes. A removed line counts where the new file
    goes on after it."""
    touched = [number for number, _ in file.added()]
    for hunk in file.hunks:
        for index, line in enumerate(hunk.lines):
            if line.kind == "-":
                after = next((later.new for later in hunk.lines[index:] if later.new is not None), None)
                if after is not None:
                    touched.append(after)
    if not touched:
        return []
    targets = []

    def span(node: ast.AST) -> tuple[int, int]:
        start = min([node.lineno] + [d.lineno for d in getattr(node, "decorator_list", [])])
        return start, node.end_lineno or node.lineno

    def add(node: ast.FunctionDef | ast.AsyncFunctionDef, name: str, method: str, self_names: dict[int, str]) -> None:
        start, end = span(node)
        count = sum(start <= number <= end for number in touched)
        if count and not any(_decorator_name(d) in _CALL_DECORATORS for d in node.decorator_list):
            targets.append(_Target(file.path, name, method, start, end, count, self_names))

    for node in tree.body:
        if isinstance(node, (ast.FunctionDef, ast.AsyncFunctionDef)):
            add(node, node.name, "", {})
        elif isinstance(node, ast.ClassDef):
            methods = [item for item in node.body if isinstance(item, (ast.FunctionDef, ast.AsyncFunctionDef))]
            self_names = {item.lineno: name for item in methods if (name := _first_argument(item))}
            for item in methods:
                if item.name == "__init__" or not (item.name.startswith("__") and item.name.endswith("__")):
                    add(item, node.name, item.name, self_names)
    return targets


def _decorator_name(node: ast.AST) -> str:
    while isinstance(node, ast.Call):
        node = node.func
    return node.attr if isinstance(node, ast.Attribute) else node.id if isinstance(node, ast.Name) else ""


def _first_argument(method: ast.FunctionDef | ast.AsyncFunctionDef) -> str:
    """`self` or `cls`, for a method that has one and rebinds it nowhere."""
    if any(_decorator_name(d) == "staticmethod" for d in method.decorator_list):
        return ""
    args = method.args.posonlyargs + method.args.args
    if not args:
        return ""
    name = args[0].arg
    bound = sum(1 for node in ast.walk(method) if (isinstance(node, ast.arg) and node.arg == name)
                or (isinstance(node, ast.Name) and node.id == name and not isinstance(node.ctx, ast.Load))
                or (isinstance(node, (ast.Global, ast.Nonlocal)) and name in node.names))
    return name if bound == 1 else ""


# ── who calls it ─────────────────────────────────────────────────────────


def _mentions(repo: _Repo, targets: list[_Target]) -> list[str]:
    """The Python files whose text has one of the top-level names a call
    found here needs: the function's, or its class's."""
    words = {t.name for t in targets}  # every call found has it: the function, or the class of a method
    pattern = re.compile(r"(?<!\w)(?:" + "|".join(sorted(map(re.escape, words))) + r")(?!\w)")
    paths = sorted(repo.python)
    readers = []
    with ThreadPoolExecutor(max_workers=8) as pool:
        for at in range(0, len(paths), 256):
            batch = paths[at:at + 256]
            for path, text in zip(batch, pool.map(lambda p: repo.text(p) if p in repo.changed else _read(repo.repo_dir, p), batch)):
                if text is not None and not _minified(text) and pattern.search(text):
                    readers.append(path)
    # The changed modules first: their own calls are the likeliest, and the cut mustn't drop them.
    own = {t.path for t in targets}
    return sorted(readers, key=lambda p: p not in own)[:MAX_READ]


class _Reader:
    """The repository's files parsed once each, and what a name of a module is."""

    def __init__(self, repo: _Repo):
        self.repo = repo
        self._trees: dict[str, ast.Module | None] = {}
        self._origins: dict[tuple[str, str], tuple[str, str] | None] = {}

    def tree(self, path: str) -> ast.Module | None:
        if path not in self._trees:
            text = self.repo.text(path)
            self._trees[path] = _parse(text) if text is not None else None
        return self._trees[path]

    def origin(self, module: str | None, name: str, depth: int = 0) -> tuple[str, str] | None:
        """The (module, name) whose `def` or `class` the name `name` of
        `module` is: the module itself when only that statement binds it,
        anywhere in the file, or else what the one import that binds it
        there names. None when that isn't certain."""
        if not module or depth > MAX_DEPTH:
            return None
        key = (module, name)
        if key not in self._origins:
            self._origins[key] = None  # a circle of re-exports ends here
            self._origins[key] = self._origin(module, name, depth)
        return self._origins[key]

    def _origin(self, module: str, name: str, depth: int) -> tuple[str, str] | None:
        tree = self.tree(module)
        if tree is None:
            return None
        imports, _, star = _bindings(tree)
        if star or _binding_counts(tree).get(name) != 1:
            return None
        if any(isinstance(node, (ast.FunctionDef, ast.AsyncFunctionDef, ast.ClassDef)) and node.name == name for node in tree.body):
            return module, name
        [binding] = imports.get(name) or [None]
        if binding is not None and binding[0] == "from":
            _, level, source, original = binding
            return self.origin(self.repo.resolve(module, level, source), original, depth + 1)
        return None

    def submodule(self, package: str, name: str) -> str | None:
        """`package.name` where it's the submodule: the package's own text binds no `name`."""
        if not _is_init(package):
            return None
        tree = self.tree(package)
        if tree is None or name in _binding_counts(tree):
            return None
        return self.repo.file_in(package.rpartition("/")[0], [name])


def _calls(reader: _Reader, path: str, tree: ast.Module, targets: list[_Target]) -> list[tuple[_Target, ast.Call]]:
    """The calls in one file that are certain to be one of the targets."""
    repo = reader.repo
    imports, other, star = _bindings(tree)
    if star:  # any name could come from it
        return []
    counts = _binding_counts(tree)
    by_name: dict[tuple[str, str], list[_Target]] = {}
    for target in targets:
        by_name.setdefault((target.path, target.name), []).append(target)
    wanted = {target.name for target in targets}
    names: dict[str, tuple[str, str]] = {}  # a name in this file -> the (module, top-level name) it is
    modules: dict[str, str] = {}  # a name in this file -> the module of the repository it is
    for name, bindings in imports.items():
        if name in other or len(bindings) != 1:
            continue
        [binding] = bindings
        if binding[0] == "from" and binding[3] in wanted:
            _, level, module, original = binding
            origin = reader.origin(repo.resolve(path, level, module), original)
            if origin in by_name:
                names[name] = origin
                continue
        where = _module_of(repo, path, binding)
        if where and where[0] == "repo":
            modules[name] = where[1]
    for target in targets:
        if target.path == path and counts.get(target.name) == 1 and target.name not in imports:
            names[target.name] = (path, target.name)  # its own module, where only the `def` binds it
    found: list[tuple[_Target, ast.Call]] = []
    classes = {id(item): node for node in tree.body if isinstance(node, ast.ClassDef)
               for item in node.body if isinstance(item, (ast.FunctionDef, ast.AsyncFunctionDef))}
    for method, call in _calls_by_method(tree):
        func = call.func
        if isinstance(func, ast.Name) and func.id in names:
            for target in by_name[names[func.id]]:
                if not target.method or target.method == "__init__":
                    found.append((target, call))
        elif isinstance(func, ast.Attribute) and isinstance(func.value, ast.Name):
            root = func.value.id
            if root in names:  # `Class.method(...)`
                found += [(t, call) for t in by_name[names[root]] if t.method == func.attr]
            elif root in modules and func.attr in wanted:  # `module.function(...)`, `module.Class(...)`
                origin = reader.origin(modules[root], func.attr)
                found += [(t, call) for t in by_name.get(origin, []) if not t.method or t.method == "__init__"] if origin else []
            elif method is not None and path in {t.path for t in targets}:  # `self.method(...)` in the class's own methods
                cls = classes.get(id(method))
                for target in by_name.get((path, cls.name), []) if cls is not None else []:
                    if target.method == func.attr and target.self_names.get(method.lineno) == root:
                        found.append((target, call))
        elif isinstance(func, ast.Attribute) and isinstance(func.value, ast.Attribute) and isinstance(func.value.value, ast.Name):
            root = func.value.value.id  # `module.Class.method(...)` or `package.module.function(...)`
            if root in modules and func.value.attr in wanted:
                inner = reader.origin(modules[root], func.value.attr)
                found += [(t, call) for t in by_name.get(inner, []) if t.method == func.attr] if inner else []
            elif root in modules and func.attr in wanted:
                sub = reader.submodule(modules[root], func.value.attr)
                origin = reader.origin(sub, func.attr) if sub else None
                found += [(t, call) for t in by_name.get(origin, []) if not t.method or t.method == "__init__"] if origin else []
    return found


def _calls_by_method(tree: ast.Module) -> list[tuple[ast.AST | None, ast.Call]]:
    """Every call in the file, with the top-level class's method it's in (or None)."""
    found: list[tuple[ast.AST | None, ast.Call]] = []
    for node in tree.body:
        methods = [item for item in node.body if isinstance(item, (ast.FunctionDef, ast.AsyncFunctionDef))] if isinstance(node, ast.ClassDef) else []
        inside = {id(call) for item in methods for call in ast.walk(item) if isinstance(call, ast.Call)}
        for item in methods:
            found += [(item, call) for call in ast.walk(item) if isinstance(call, ast.Call)]
        found += [(None, call) for call in ast.walk(node) if isinstance(call, ast.Call) and id(call) not in inside]
    return found


def _binding_counts(tree: ast.Module) -> dict[str, int]:
    """How many statements, arguments and imports bind each name, anywhere in the file."""
    counts: dict[str, int] = {}

    def bind(name: str) -> None:
        counts[name] = counts.get(name, 0) + 1

    for node in ast.walk(tree):
        if isinstance(node, (ast.FunctionDef, ast.AsyncFunctionDef, ast.ClassDef)):
            bind(node.name)
        elif isinstance(node, ast.Name) and not isinstance(node.ctx, ast.Load):
            bind(node.id)
        elif isinstance(node, ast.arg):
            bind(node.arg)
        elif isinstance(node, (ast.Import, ast.ImportFrom)):
            for alias in node.names:
                bind(alias.asname or alias.name.partition(".")[0])
        elif isinstance(node, (ast.ExceptHandler, ast.MatchAs, ast.MatchStar)) and node.name:
            bind(node.name)
        elif isinstance(node, (ast.Global, ast.Nonlocal)):
            for name in node.names:
                bind(name)
    return counts


# ── how a call is shown ──────────────────────────────────────────────────


def _numbered(lines: list[str], call: ast.Call) -> str:
    start = max(1, call.lineno - LINES_BEFORE)
    end = min(len(lines), call.end_lineno or call.lineno, start + MAX_SITE_LINES - 1)
    return "\n".join(f"{n:>6}    {lines[n - 1].rstrip()[:MAX_LINE_CHARS]}" for n in range(start, end + 1))


def _within(tree: ast.Module, line: int) -> str:
    """The innermost function the line is in."""
    around = [node for node in ast.walk(tree)
              if isinstance(node, (ast.FunctionDef, ast.AsyncFunctionDef)) and node.lineno <= line <= (node.end_lineno or node.lineno)]
    return max(around, key=lambda node: node.lineno).name if around else ""


def _is_test(path: str) -> bool:
    parts = path.split("/")
    return parts[-1].startswith("test") or parts[-1].endswith("_test.py") or any(part in ("tests", "test", "testing") for part in parts[:-1])


def block(callers: list[Callers], paths: set[str] | None, max_tokens: int) -> str:
    """The prompt's section for the changed files in `paths` (None: all),
    cut at whole calls to fit `max_tokens`, TAIL_TOKENS of it kept for the
    count of what was left out. Empty when there's nothing."""
    from stratum_open.diff import approx_tokens

    entries = [c for c in callers if paths is None or c.path in paths]
    if not entries or max_tokens <= 0:
        return ""
    text = ("\nWhere the functions this PR changes are called from, elsewhere in the repository. This code isn't part of the diff: "
            "use it to check that each change still fits how it is called, and report only lines of the diff.\n")
    left, any_shown = 0, False
    for entry in entries:
        title = f"`{entry.name}` (`{entry.path}`, line {entry.line}) is called from:\n"
        shown = False
        for site in entry.sites:
            piece = ("" if shown else title) + f"{site.path}:{site.line}" + (f", in `{site.within}`" if site.within else "") + ":\n" + site.code + "\n"
            if approx_tokens(text + piece) + TAIL_TOKENS > max_tokens:
                left += 1
                continue
            text += piece
            shown = any_shown = True
        left += entry.more
    if not any_shown:
        return ""
    return text + (f"({left} more calls not shown.)\n" if left else "")
