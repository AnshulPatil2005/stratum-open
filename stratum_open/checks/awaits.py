"""A coroutine called without `await` in Python.

`f()` inside an `async def`, where `f` is a coroutine, makes a coroutine
object and runs nothing: no error, just a "never awaited" warning if anyone is
looking. Without types we can't know `f` is a coroutine, but the file says so
when the same call is awaited elsewhere in it, or when the PR's removed lines
awaited it. So a call is flagged when:

- it's on an added line inside an `async def` (not inside a nested `def` or
  `lambda`),
- the same callee (`self._fin.close`, `ensure_awaitable`) is awaited
  somewhere in the file, or on a line the PR removed,
- and its value isn't awaited, passed to another call (`create_task(f())`,
  `gather(f())`), yielded, or put in a name that is awaited later.

With the file's full text the code is parsed; without it only single lines are
read, so less is caught.
"""

from __future__ import annotations

import ast
import re

from stratum_open.checks.common import extension
from stratum_open.diff import FileDiff
from stratum_open.findings import Finding

RULE = "code/missing-await"
_AWAITED = re.compile(r"\bawait\s+([A-Za-z_][\w.]*)\s*\(")
_ASYNC_DEF = re.compile(r"^\s*async\s+def\s")


def check(files: list[FileDiff], contents: dict[str, str] | None = None) -> list[Finding]:
    findings: list[Finding] = []
    for file in files:
        if extension(file.path) != ".py" or file.status == "deleted":
            continue
        added = {number for number, _ in file.added()}
        if not added:
            continue
        # Each removed await, with a new-file line from its hunk to place it in a class.
        removed = [(_anchor(hunk), m.group(1)) for hunk in file.hunks for line in hunk.lines
                   if line.kind == "-" for m in _AWAITED.finditer(line.text)]
        text = (contents or {}).get(file.path)
        tree = _parse(text) if text is not None else None
        if tree is not None:
            hits = _from_tree(tree, added, removed)
        else:
            hits = _from_lines(file, added)
        findings.extend(_finding(file.path, number, name) for number, name in sorted(set(hits)))
    return findings


def touches_async(file: FileDiff) -> bool:
    """Whether a Python file's change is in or near async code, so its full
    text is worth fetching for this check."""
    return any("async def" in hunk.header for hunk in file.hunks) or any(
        "await" in line.text or "async " in line.text for hunk in file.hunks for line in hunk.lines
    )


def _parse(text: str) -> ast.Module | None:
    try:
        return ast.parse(text)
    except (SyntaxError, ValueError):
        return None


def _name(node: ast.AST) -> str | None:
    """`a.b.c` for a Name/Attribute chain, else None."""
    parts = []
    while isinstance(node, ast.Attribute):
        parts.append(node.attr)
        node = node.value
    if not isinstance(node, ast.Name):
        return None
    parts.append(node.id)
    return ".".join(reversed(parts))


def _awaits(node: ast.AST) -> set[str]:
    return {name for n in ast.walk(node) if isinstance(n, ast.Await) and isinstance(n.value, ast.Call)
            for name in [_name(n.value.func)] if name}


def _anchor(hunk) -> int:
    """A new-file line in the hunk, near its removed lines."""
    return next((line.new for line in hunk.lines if line.new is not None), hunk.new_start)


def _own(name: str) -> bool:
    return name.startswith(("self.", "cls."))


def _from_tree(tree: ast.Module, added: set[int], removed: list[tuple[int, str]]) -> list[tuple[int, str]]:
    """`self.x.y` means something different in each class (one class's
    `self._file` can be a sync file, another's an async one), so those names
    count as awaited only within their class, and a removed one only in the
    class its hunk is in; other names, file-wide."""
    module_wide = {n for n in _awaits(tree) if not _own(n)} | {n for _, n in removed if not _own(n)}
    hits = []
    scopes: list[tuple[ast.AST, set[str]]] = [(tree, set())]
    while scopes:
        scope, own = scopes.pop()
        for node in ast.iter_child_nodes(scope):
            if isinstance(node, ast.ClassDef):
                span = range(node.lineno, (node.end_lineno or node.lineno) + 1)
                mine = {n for n in _awaits(node) if _own(n)} | {n for at, n in removed if _own(n) and at in span}
                scopes.append((node, mine))
            elif isinstance(node, ast.AsyncFunctionDef):
                hits.extend(_in_async(node, added, module_wide | own))
                scopes.append((node, own))
            elif isinstance(node, (ast.FunctionDef, ast.If, ast.Try, ast.With, ast.AsyncWith)):
                scopes.append((node, own))
    return hits


def _in_async(func: ast.AsyncFunctionDef, added: set[int], awaited: set[str]) -> list[tuple[int, str]]:
    """Unawaited calls in one async function's own body."""
    parents: dict[ast.AST, ast.AST] = {}
    stack: list[ast.AST] = list(func.body)
    for child in func.body:
        parents[child] = func
    calls = []
    while stack:
        node = stack.pop()
        if isinstance(node, (ast.FunctionDef, ast.AsyncFunctionDef, ast.Lambda, ast.ClassDef)):
            continue  # its own scope: a nested sync def can't await, a nested async def is walked on its own
        if isinstance(node, ast.Call):
            calls.append(node)
        for child in ast.iter_child_nodes(node):
            parents[child] = node
            stack.append(child)
    awaited_names = {n.value.id for n in ast.walk(func) if isinstance(n, ast.Await) and isinstance(n.value, ast.Name)}
    hits = []
    for call in calls:
        name = _name(call.func)
        if name not in awaited or call.lineno not in added:
            continue
        parent = parents.get(call)
        if isinstance(parent, (ast.Await, ast.Call, ast.keyword, ast.Yield, ast.YieldFrom, ast.Starred)):
            continue  # awaited, or handed to something that takes a coroutine (create_task, gather)
        if isinstance(parent, (ast.Assign, ast.AnnAssign)):
            targets = parent.targets if isinstance(parent, ast.Assign) else [parent.target]
            if any(isinstance(t, ast.Name) and t.id in awaited_names for t in targets):
                continue  # `fut = f()` ... `await fut`
        if isinstance(parent, (ast.List, ast.Tuple, ast.Set, ast.Dict, ast.comprehension, ast.ListComp, ast.GeneratorExp)):
            continue  # collected, most likely for gather()
        hits.append((call.lineno, name))
    return hits


def _from_lines(file: FileDiff, added: set[int]) -> list[tuple[int, str]]:
    """No full text, so only the plainest case: in a hunk that shows it's
    inside an `async def`, an added line whose whole statement is a call
    (`f(...)`, `x = f(...)`, `return f(...)`) that a removed line of the
    same hunk awaited. A call nested in another (`create_task(f())`) or in a
    collection can't be told apart from one handed on, so it's left alone."""
    hits = []
    for hunk in file.hunks:
        awaited = {m.group(1) for line in hunk.lines if line.kind == "-" for m in _AWAITED.finditer(line.text)}
        if not awaited:
            continue
        in_async = "async def" in hunk.header
        for line in hunk.lines:
            if line.new is not None and _ASYNC_DEF.match(line.text):
                in_async = True
            if line.kind != "+" or line.new not in added or not in_async or "await" in line.text:
                continue
            for name in awaited:
                if re.match(rf"\s*(?:return\s+|[\w.]+(?:\s*,\s*[\w.]+)*\s*=\s*)?{re.escape(name)}\s*\(", line.text):
                    hits.append((line.new, name))
    return hits


SYNC_FILE_RULE = "code/await-sync-file"
_FILE_METHODS = {"read", "readline", "readlines", "write", "writelines", "close", "flush", "seek", "tell", "truncate"}


def check_sync_files(files: list[FileDiff], contents: dict[str, str] | None = None) -> list[Finding]:
    """The opposite mistake: `await` on a method of a plain file from the
    builtin `open()`. Its `read()` returns bytes and `close()` None, so the
    `await` raises TypeError. Needs the full text, and stays quiet in a file
    that defines or imports its own `open` (aiofiles, anyio)."""
    findings: list[Finding] = []
    for file in files:
        if extension(file.path) != ".py" or file.status == "deleted":
            continue
        added = {number for number, _ in file.added()}
        tree = _parse((contents or {}).get(file.path) or "") if added else None
        if tree is None or _shadows_open(tree):
            continue
        for scope in [tree, *(n for n in ast.walk(tree) if isinstance(n, (ast.ClassDef, ast.FunctionDef, ast.AsyncFunctionDef)))]:
            plain = _plain_files(scope)
            if not plain:
                continue
            for node in ast.walk(scope):
                if (isinstance(node, ast.Await) and isinstance(node.value, ast.Call) and isinstance(node.value.func, ast.Attribute)
                        and node.value.func.attr in _FILE_METHODS and _name(node.value.func.value) in plain and node.lineno in added):
                    findings.append(_sync_file_finding(file.path, node.lineno, f"{_name(node.value.func.value)}.{node.value.func.attr}"))
    unique = {(f.path, f.line_start): f for f in findings}
    return list(unique.values())


def _shadows_open(tree: ast.Module) -> bool:
    for node in ast.walk(tree):
        if isinstance(node, (ast.Import, ast.ImportFrom)) and any((a.asname or a.name) == "open" for a in node.names):
            return True
        if isinstance(node, ast.Assign) and any(isinstance(t, ast.Name) and t.id == "open" for t in node.targets):
            return True
    return any(isinstance(n, (ast.FunctionDef, ast.AsyncFunctionDef)) and n.name == "open" for n in tree.body)


def _plain_files(scope: ast.AST) -> set[str]:
    """Names (`f`, `self._fin`) this scope binds to the builtin `open()`."""
    def is_open(call: ast.AST) -> bool:
        return isinstance(call, ast.Call) and _name(call.func) in ("open", "io.open")
    names = set()
    for node in ast.walk(scope):
        if isinstance(node, ast.Assign) and is_open(node.value):
            names |= {n for t in node.targets for n in [_name(t)] if n}
        elif isinstance(node, ast.With):  # a sync `with open(...) as f`; `async with` is someone else's open
            names |= {n for item in node.items if is_open(item.context_expr) and item.optional_vars is not None
                      for n in [_name(item.optional_vars)] if n}
    return names


def _sync_file_finding(path: str, number: int, call: str) -> Finding:
    return Finding(
        title=f"`await {call}()` on a plain file",
        detail=(
            f"`{call.rsplit('.', 1)[0]}` comes from the builtin `open()`, whose methods aren't coroutines: "
            f"`{call}()` returns its result directly, so awaiting it raises TypeError."
        ),
        severity="high", category="bug", path=path, line_start=number, line_end=number,
        suggestion="Drop the `await`, or open the file with an async library (aiofiles, anyio) if it must not block.",
        source="check", rule=SYNC_FILE_RULE, confidence=0.9,
    )


def _finding(path: str, number: int, name: str) -> Finding:
    return Finding(
        title=f"`{name}()` isn't awaited",
        detail=(
            f"`{name}()` is awaited elsewhere in this file, so it's a coroutine (or returns an awaitable), "
            "but here inside an `async def` it's called without `await`. That only creates the coroutine: "
            "its code never runs, and the value used is a coroutine object rather than its result."
        ),
        severity="medium", category="bug", path=path, line_start=number, line_end=number,
        suggestion=f"Write `await {name}(...)`, or pass the coroutine to `asyncio.create_task` if it's meant to run in the background.",
        source="check", rule=RULE, confidence=0.8,
    )
