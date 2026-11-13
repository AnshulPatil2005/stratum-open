"""A promise dropped in JavaScript or TypeScript.

`save()` on its own inside an `async` function, where `save` returns a
promise, starts the work and lets go of it: nothing waits for it to finish,
and if it fails the error goes nowhere. Without types we can't know `save`
returns a promise, but the file says so when the same call is awaited
elsewhere in it, when the PR's removed lines awaited it, or when the file
declares it `async`. So a call is flagged when:

- it starts on an added line, in the body of an `async` function, method or
  arrow function (not in a function nested there that isn't `async`),
- it is a whole statement, `save();`. One that is awaited, returned, assigned,
  passed to another call, chained (`.then`, `.catch`), marked `void` or put in
  an array has been handed on, and is left alone,
- and the same callee (`save`, `this.save`, `db.save`) is awaited somewhere
  in the file or on a line the PR removed, or is declared `async` in the file.

The file is evidence the other way too, and then nothing is said: the callee
is already called on its own on a line the PR leaves alone, or more than half
as often as it's awaited (`app.register()` can be awaited and needn't be); or
this call hands over a callback and the awaited ones don't (`app.listen(port,
done)` beside `await app.listen(port)`).

A name means what it's bound to. `this.save` counts only within its class or
object literal, a bare name that is a parameter or a local (`next`, `done`)
only within its function, and `db.save` not where one `db` hides another.

The parser is tree-sitter, from its wheels (the `js` extra: `tree-sitter`,
`tree-sitter-javascript` and `tree-sitter-typescript`, all MIT, about 1.1 MB
to download). Python has no JavaScript parser of its own, and a tokenizer
written here would have to tell a regex from a division, a generic's `<` from
a comparison, JSX text from code and `${}` in a template from a string before
it could say where an `async` function ends or whether a call is a whole
statement: a parser in all but name, and wrong without saying so where it
gives up. Without the extra the check says nothing, as with Bandit. The text
is only parsed, in this process: nothing in it is run.

It needs the file's full text. A file the grammar can't parse cleanly is
skipped.
"""

from __future__ import annotations

import functools
import re
from dataclasses import dataclass, field
from typing import Any, Iterator

from stratum_open.checks.common import extension
from stratum_open.diff import FileDiff
from stratum_open.findings import Finding

RULE = "code/floating-promise"
MAX_CHARS = 1_000_000  # the contents API's limit for a file's text; a bigger one is generated code
_GRAMMARS = {
    ".js": "javascript", ".jsx": "javascript", ".mjs": "javascript", ".cjs": "javascript",
    ".ts": "typescript", ".mts": "typescript", ".cts": "typescript", ".tsx": "tsx",
}
_FUNCTIONS = frozenset({
    "function_declaration", "function_expression", "function", "arrow_function", "method_definition",
    "generator_function_declaration", "generator_function",
})
_OWN = ("this.", "super.")
_CALLBACKS = frozenset({"callback", "cb", "done", "next"})
_NAME = r"#?[A-Za-z_$][\w$]*"
_AWAITED = re.compile(rf"\bawait\s+({_NAME}(?:\s*\??\.\s*{_NAME})*)\s*\(")
_KEYWORDS = frozenset({
    "if", "for", "while", "switch", "catch", "function", "return", "await", "typeof", "void", "delete", "new", "throw",
    "yield", "import", "with", "do", "else", "case", "in", "of", "async", "class", "export",
})
_STARTS_WITH_CALL = re.compile(rf"\s*({_NAME})(?:\s*\??\.\s*{_NAME})*\s*\(")


@functools.lru_cache(maxsize=1)
def _languages() -> dict[str, Any] | None:
    """The grammars by name, or None when the extra isn't installed or its
    packages don't fit each other."""
    try:
        import tree_sitter
        import tree_sitter_javascript
        import tree_sitter_typescript

        return {
            "javascript": tree_sitter.Language(tree_sitter_javascript.language()),
            "typescript": tree_sitter.Language(tree_sitter_typescript.language_typescript()),
            "tsx": tree_sitter.Language(tree_sitter_typescript.language_tsx()),
        }
    except Exception:  # not installed, or a grammar built for another tree-sitter: either way, no parser
        return None


def available() -> bool:
    return _languages() is not None


def reads(path: str) -> bool:
    return extension(path) in _GRAMMARS


def worth_reading(file: FileDiff) -> bool:
    """Whether a JavaScript or TypeScript file adds a line that starts with a
    call, almost the only place a finding can be, so its full text is worth
    fetching for this check."""
    if not reads(file.path) or file.status == "deleted" or file.binary:
        return False
    return any(match and match.group(1) not in _KEYWORDS for match in (_STARTS_WITH_CALL.match(text) for _, text in file.added()))


def check(files: list[FileDiff], contents: dict[str, str] | None = None) -> list[Finding]:
    if not contents or not available():
        return []
    findings: list[Finding] = []
    for file in files:
        text = contents.get(file.path)
        added = {number for number, _ in file.added()}
        if text is None or not added or not reads(file.path) or file.status == "deleted" or len(text) > MAX_CHARS:
            continue
        root = parse(file.path, text)
        if root is None:
            continue
        # Each removed await, with a new-file line from its hunk to place it in a class or function.
        removed = [(_anchor(hunk), re.sub(r"[\s?]", "", m.group(1))) for hunk in file.hunks for line in hunk.lines
                   if line.kind == "-" for m in _AWAITED.finditer(line.text)]
        seen = _read(root)
        hits = {(call.line, call.name): why for calls in seen.calls.values() for call in calls if call.line in added
                for why in [_evidence(call, seen, removed, added)] if why}
        findings.extend(_finding(file.path, number, name, why) for (number, name), why in sorted(hits.items()))
    return findings


def parse(path: str, text: str) -> Any | None:
    """The file's syntax tree, or None when any of it didn't parse: the tree
    around a syntax error is a guess, and a guess is no evidence."""
    import tree_sitter

    parser = tree_sitter.Parser(_languages()[_GRAMMARS[extension(path)]])
    tree = parser.parse(text.encode("utf-8", errors="replace"))
    return None if tree is None or tree.root_node.has_error else tree.root_node


def _anchor(hunk) -> int:
    """A new-file line in the hunk, near its removed lines."""
    return next((line.new for line in hunk.lines if line.new is not None), hunk.new_start)


@dataclass(eq=False)
class _Function:
    """One function: the names it binds (parameters and locals), and the function around it."""
    outer: _Function | None
    is_async: bool
    binds: set[str]
    lines: range

    def binder(self, name: str) -> _Function | None:
        """The function a name belongs to here, or None for the module's."""
        scope: _Function | None = self
        while scope is not None and name not in scope.binds:
            scope = scope.outer
        return scope

    def inside(self, other: _Function) -> bool:
        """Whether this is `other` or a function within it."""
        scope: _Function | None = self
        while scope is not None and scope is not other:
            scope = scope.outer
        return scope is not None


@dataclass
class _Call:
    name: str
    line: int
    callback: bool  # its last argument is a callback
    owner: int | None  # the class body or object literal `this` belongs to, when that's known
    function: _Function | None

    @property
    def scope(self) -> _Function | int | None:
        """Where the name is bound: `this.` names in their class or object,
        others in the function that binds their first part, or the module."""
        if self.name.startswith(_OWN):
            return self.owner
        return self.function.binder(self.name.split(".")[0]) if self.function is not None else None


@dataclass
class _Seen:
    calls: dict[str, list[_Call]] = field(default_factory=dict)  # whole-statement calls in async functions, by name
    awaits: dict[str, list[_Call]] = field(default_factory=dict)
    owners: dict[int, range] = field(default_factory=dict)
    # What each name is bound to in its scope: an async function (its lines),
    # a function that isn't async, or anything else.
    declared: dict[tuple[Any, str], list[int]] = field(default_factory=dict)
    sync: set[tuple[Any, str]] = field(default_factory=set)
    other: set[tuple[Any, str]] = field(default_factory=set)

    def declare(self, scope: Any, name: str, value: Any | None, line: int) -> None:
        if value is None or value.type not in _FUNCTIONS or not value.is_named:
            self.other.add((scope, name))
        elif _is_async(value) and not any(child.type in ("*", "get", "set") for child in value.children if not child.is_named):
            self.declared.setdefault((scope, name), []).append(line)
        else:
            self.sync.add((scope, name))  # a generator or a getter doesn't return a promise when called, either

    def declared_async(self, scope: Any, name: str) -> int | None:
        """The line that declares a name `async`, when that's all its scope binds it to."""
        lines = self.declared.get((scope, name), [])
        return lines[0] if len(lines) == 1 and (scope, name) not in self.sync | self.other else None


def _line(node: Any) -> int:
    return node.start_point[0] + 1


def _lines(node: Any) -> range:
    return range(_line(node), node.end_point[0] + 2)


def _text(node: Any) -> str:
    return node.text.decode("utf-8", errors="replace")


def _is_async(node: Any) -> bool:
    return any(child.type == "async" for child in node.children if not child.is_named)


def _name(node: Any) -> str | None:
    """`a.b.c` for a chain of names off an identifier, `this` or `super`
    (`a?.b` and `a!.b` read the same), else None."""
    parts: list[str] = []
    while True:
        if node.type == "non_null_expression" and node.named_child_count == 1:
            node = node.named_children[0]
        elif node.type == "member_expression":
            prop, node = node.child_by_field_name("property"), node.child_by_field_name("object")
            if prop is None or node is None or prop.type not in ("property_identifier", "private_property_identifier"):
                return None
            parts.append(_text(prop))
        elif node.type in ("identifier", "this", "super") and (parts or node.type == "identifier"):
            parts.append(_text(node))
            return ".".join(reversed(parts))
        else:
            return None


def _callee(call: Any | None) -> str | None:
    """The name a call calls, when the call is a plain one: not a tagged
    template, not a call on another call's result, and not a promise being
    handled (`pending.catch(...)`)."""
    if call is None or call.type != "call_expression":
        return None
    function, arguments = call.child_by_field_name("function"), call.child_by_field_name("arguments")
    name = _name(function) if function is not None and arguments is not None and arguments.type == "arguments" else None
    return None if name is None or name.rsplit(".", 1)[-1] in ("then", "catch", "finally") else name


def awaited_call(node: Any) -> tuple[str | None, Any | None]:
    """The call an `await` waits for and its callee's name, if it's a plain one."""
    inner = node.named_children[0] if node.named_child_count else None
    parent = node.parent
    # The TypeScript grammar reads `await f<T>(x)` as `(await f)<T>(x)`.
    if (inner is not None and parent is not None and parent.type == "call_expression" and parent.child_by_field_name("type_arguments") is not None
            and parent.child_by_field_name("function").id == node.id):
        return _name(inner), parent
    return _callee(inner), inner


def _takes_callback(call: Any) -> bool:
    """Whether a call's last argument is a function, or named like one that
    is called back: the form of an API where the callback does the waiting."""
    given = [child for child in call.child_by_field_name("arguments").named_children if child.type != "comment"]
    if not given:
        return False
    return given[-1].type in ("arrow_function", "function_expression", "function") or (given[-1].type == "identifier" and _text(given[-1]) in _CALLBACKS)


def _bound(pattern: Any) -> Iterator[str]:
    """The names a parameter list or a declaration's left side binds."""
    stack = [pattern]
    while stack:
        node = stack.pop()
        if node.type in ("identifier", "shorthand_property_identifier_pattern"):
            yield _text(node)
        elif node.type in ("required_parameter", "optional_parameter"):
            stack.extend(n for n in [node.child_by_field_name("pattern")] if n is not None)
        elif node.type in ("assignment_pattern", "object_assignment_pattern"):
            stack.extend(n for n in [node.child_by_field_name("left")] if n is not None)
        elif node.type == "pair_pattern":
            stack.extend(n for n in [node.child_by_field_name("value")] if n is not None)
        elif node.type in ("formal_parameters", "object_pattern", "array_pattern", "rest_pattern"):
            stack.extend(node.named_children)


def _members(node: Any) -> Iterator[tuple[str, Any | None, int]]:
    """The members of a class body or an object literal that have a plain
    name: the name, what it holds, and its line."""
    for member in node.named_children:
        if member.type == "method_definition":
            name, value = member.child_by_field_name("name"), member
        elif member.type == "pair":
            name, value = member.child_by_field_name("key"), member.child_by_field_name("value")
        elif member.type in ("public_field_definition", "field_definition"):
            name, value = member.child_by_field_name("name") or member.child_by_field_name("property"), member.child_by_field_name("value")
        else:
            continue
        if name is not None and name.type in ("property_identifier", "private_property_identifier"):
            yield _text(name), value, _line(member)


def _read(root: Any) -> _Seen:
    seen = _Seen()
    assigned: list[tuple[_Call, Any]] = []
    stack: list[tuple[Any, _Function | None, int | None]] = [(root, None, None)]
    while stack:
        node, function, owner = stack.pop()
        kind = node.type
        if kind in ("class_body", "object"):
            seen.owners[node.id] = _lines(node)
            for name, value, line in _members(node):
                seen.declare(node.id, "this." + name, value, line)
            if kind == "class_body":
                owner = node.id  # an object's own `this` starts in its methods, below
        elif kind == "class_static_block":
            function = _Function(function, False, set(), _lines(node))
        elif kind in _FUNCTIONS and node.is_named:
            parent = node.parent
            if kind == "method_definition":
                if parent is not None and parent.type == "object":
                    owner = parent.id
            elif kind != "arrow_function":  # an arrow function's `this` is the one around it
                if parent is not None and parent.type == "pair" and parent.parent is not None and parent.parent.type == "object":
                    owner = parent.parent.id
                elif parent is None or parent.type not in ("public_field_definition", "field_definition"):
                    owner = None  # a plain function's `this` is whatever it's called on
                name = node.child_by_field_name("name")
                if name is not None and kind in ("function_declaration", "generator_function_declaration"):
                    seen.declare(function, _text(name), node, _line(node))
                    if function is not None:
                        function.binds.add(_text(name))
            params = node.child_by_field_name("parameters") or node.child_by_field_name("parameter")
            function = _Function(function, _is_async(node), set(_bound(params)) if params is not None else set(), _lines(node))
        elif kind in ("variable_declarator", "for_in_statement", "catch_clause"):
            target = node.child_by_field_name("name") or node.child_by_field_name("left") or node.child_by_field_name("parameter")
            names = list(_bound(target)) if target is not None else []
            if function is not None:
                function.binds.update(names)
            value = node.child_by_field_name("value")
            if kind == "variable_declarator" and target is not None and target.type == "identifier":
                seen.declare(function, names[0], value, _line(node))
                if value is not None and value.type == "object":  # `const api = { async load() {} }` declares `api.load()`
                    for name, member, line in _members(value):
                        seen.declare(function, f"{names[0]}.{name}", member, line)
            else:
                seen.other.update((function, name) for name in names)
        elif kind in ("import_specifier", "namespace_import", "import_clause", "class_declaration"):
            seen.other.update((function, _text(child)) for child in node.named_children if child.type in ("identifier", "type_identifier"))
        elif kind == "assignment_expression":
            left, right = node.child_by_field_name("left"), node.child_by_field_name("right")
            name = _name(left) if left is not None else None
            if name:
                assigned.append((_Call(name, _line(node), False, owner, function), right))
        elif kind == "await_expression":
            name, call = awaited_call(node)
            if name:
                seen.awaits.setdefault(name, []).append(_Call(name, _line(node), _takes_callback(call), owner, function))
        elif kind == "expression_statement" and function is not None and function.is_async:
            call = node.named_children[0] if node.named_child_count else None
            name = _callee(call)
            if name:
                seen.calls.setdefault(name, []).append(_Call(name, _line(call), _takes_callback(call), owner, function))
        stack.extend((child, function, owner) for child in node.children)
    for target, value in assigned:  # placed last, once every function's names are known
        seen.declare(target.scope, target.name, value, target.line)
    return seen


def _evidence(call: _Call, seen: _Seen, removed: list[tuple[int, str]], added: set[int]) -> str | None:
    """What says the callee returns a promise that was meant to be waited
    for, as the start of the finding's detail, or None."""
    name, scope = call.name, call.scope
    own, bare = name.startswith(_OWN), "." not in name
    if own and scope is None:
        return None

    def same(other: _Call) -> bool:
        """Whether a call of the same name is of the same callee. Two
        functions side by side that each have a `db` are taken to mean one
        thing by `db.save`. A `db` that hides another (a local over the
        module's) is a different one."""
        theirs = other.scope
        if theirs == scope:
            return True
        if own or bare or scope is None or theirs is None:
            return False
        return not (_reaches(scope, other) or _reaches(theirs, call))

    # An await of the callback form says nothing about the other, nor the other way round.
    awaited = sorted(other.line for other in seen.awaits.get(name, []) if same(other) and other.callback == call.callback)
    alone = [other.line for other in seen.calls.get(name, []) if same(other)]
    # A callee the file calls on its own on a line the PR leaves alone, or more
    # than half as often as it awaits it, is one the project lets run.
    if any(line not in added for line in alone) or len(alone) > max(len(awaited) // 2, 1):
        return None
    if awaited:
        return f"`{name}()` is awaited on line {awaited[0]}"
    declared = seen.declared_async(scope, name)
    if declared:
        return f"`{name.removeprefix('this.')}` is declared `async` on line {declared}"
    if (scope, name) in seen.sync:
        return None  # the file declares it as a function that isn't async: the PR may have made it so
    for at, removed_name in removed:
        if removed_name != name:
            continue
        if own and at not in seen.owners.get(scope, ()):
            continue
        if bare and isinstance(scope, _Function) and at not in scope.lines:
            continue
        return f"A line this PR removes awaited `{name}()`"
    return None


def _reaches(scope: Any, call: _Call) -> bool:
    """Whether a function's binding is in reach of where a call is."""
    return call.function is not None and call.function.inside(scope)


def _finding(path: str, number: int, name: str, why: str) -> Finding:
    return Finding(
        title=f"`{name}()` isn't awaited",
        detail=(
            f"{why}, so it returns a promise, but here inside an `async` function it's called on its own. "
            "Nothing waits for it: the code after it runs before it's done, and if it fails the error is never caught."
        ),
        severity="medium", category="bug", path=path, line_start=number, line_end=number,
        suggestion=f"Write `await {name}(...)`. If it's meant to run in the background, say so with `void {name}(...)` and handle its errors with `.catch`.",
        source="check", rule=RULE, confidence=0.7,
    )
