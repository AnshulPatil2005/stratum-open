"""Errors Ruff can prove in Python code, when `checks.ruff` is on and Ruff is
installed (the `ruff` extra).

Ruff reads whole files, so like Bandit it runs only on files whose full
content the caller passed in `contents`, and of what it reports only issues
on lines the PR adds are kept. An error the PR causes on a line it doesn't
touch (a name whose import it deletes) isn't seen. Test files are skipped.

Only the rules in RULES run. Each is an error in the code itself: a name
nothing defines, a format string that raises, a `return` outside a function.
None is about style. On the merged PRs of the checks benchmark none of them
reported anything on an added line; benchmark/results/2026-11-13-ruff says
what that shows and what it doesn't, and why other rules were left out. A
file Ruff can't parse gets no findings: its syntax error is for the
project's own CI to report.

No code from the PR runs, and the PR can't steer Ruff. Ruff parses the text
and never imports it. `--isolated` keeps it from reading any config file,
the rules are named on the command line, and nothing is fixed or cached. The
files go in a temporary folder outside any checkout, under names chosen
here, and Ruff starts there with `-P`, so a `ruff.py` in the folder the
review runs from is never taken for it. `--ignore-noqa` is for the same
reason as Bandit's `--ignore-nosec`: a `# noqa` on an added line was written
by the PR's author. A finding on a line marked that way says so.

Reading no config has a price: Ruff doesn't know the project's Python
version or the names its host defines. It's told the code is for the newest
Python (TARGET) and given the few host names in HOST_NAMES. A name another
host puts in builtins is reported as undefined.

It runs as its own process with a time limit, like Bandit.
"""

from __future__ import annotations

import importlib.util
import json
import os
import re
import subprocess
import sys
import tempfile
from collections.abc import Iterable
from typing import Any

from stratum_open.checks.common import basename, checked_lines, is_test_path, moved_in
from stratum_open.diff import FileDiff
from stratum_open.findings import Finding

MAX_FILES = 20
MAX_FILE_CHARS = 1_000_000
TIMEOUT_SECONDS = 15
MIN_VERSION = "0.16"  # the first release with every rule below
# The Python Ruff takes the code to be for. From 3.14 an annotation may name a class defined further down,
# which was an error before; with the newest version, code written for it isn't reported for that.
TARGET = "py314"

_SYNTAX = "Python rejects the whole file with `SyntaxError`, so the module can't be imported."
_LOGGING = "Formatting fails when the record is written: `logging` prints a traceback to stderr and the message is lost."
# Ruff's code -> (severity, title, what goes wrong, what to do). High is for
# code that raises when it runs; medium for code that runs and does the wrong
# thing. A title ending in ":" gets the name Ruff quotes, so two undefined
# names on one line are two findings.
RULES: dict[str, tuple[str, str, str, str]] = {
    "F404": ("high", "`from __future__` import after other code",
             "A `from __future__` import has to come before every other statement. " + _SYNTAX,
             "Move it to the top of the file."),
    "F407": ("high", "Unknown `__future__` feature:",
             "`__future__` has no feature of this name. " + _SYNTAX,
             "Fix the feature's name."),
    "F501": ("high", "Broken `%` format string",
             "The format string can't be parsed, so the `%` raises `ValueError` when this line runs.",
             "Fix the placeholder, or write `%%` for a literal percent sign."),
    "F502": ("high", "`%` format needs a dict",
             "The string has named placeholders (`%(name)s`) but the values are a tuple or list, so the `%` raises `TypeError`.",
             "Pass a dict with those names."),
    "F503": ("high", "`%` format needs a tuple",
             "The string has positional placeholders (`%s`) but the values are a dict, so the `%` raises `TypeError`.",
             "Pass the values as a tuple, or name the placeholders."),
    "F505": ("high", "`%` format is missing a key",
             "The string has a named placeholder the dict has no key for, so the `%` raises `KeyError`.",
             "Add the key, or drop the placeholder."),
    "F506": ("medium", "`%` format mixes named and positional placeholders",
             "A `%` format takes its values from a tuple or from a dict, not both: with a tuple the named placeholder "
             "raises `TypeError`, and with a dict `%s` prints the whole dict.",
             "Name every placeholder, or none."),
    "F507": ("high", "`%` format has the wrong number of values",
             "The string's placeholders and the values after the `%` differ in number, so the `%` raises `TypeError`.",
             "Give one value for each placeholder."),
    "F508": ("high", "`%*` format needs a tuple",
             "A `*` width takes its number from a tuple of values, and here the values are a dict, so the `%` raises `TypeError`.",
             "Pass the values as a tuple."),
    "F509": ("high", "Unknown `%` format character",
             "The character after this `%` isn't a conversion Python knows, so the `%` raises `ValueError`.",
             "Use a real conversion such as `%s` or `%d`, or write `%%` for a literal percent sign."),
    "F521": ("high", "Broken `.format()` string",
             "The format string can't be parsed, most often an unmatched `{` or `}`, so `.format()` raises `ValueError`.",
             "Close the brace, or double it (`{{`) if it's meant literally."),
    "F524": ("high", "`.format()` is missing an argument",
             "The string has a placeholder that no argument fills, so `.format()` raises `IndexError` or `KeyError`.",
             "Pass the missing argument, or drop the placeholder."),
    "F525": ("high", "`.format()` mixes `{}` and `{0}`",
             "A format string numbers all of its placeholders or none of them; with both, `.format()` raises `ValueError`.",
             "Number every placeholder, or none."),
    "F601": ("medium", "Dictionary key repeated:",
             "A dict keeps one value per key, so the earlier value for this key is silently dropped.",
             "Remove one of them, or fix the key that was meant to be different."),
    "F602": ("medium", "Dictionary key repeated:",
             "A dict keeps one value per key, so the earlier value for this key is silently dropped.",
             "Remove one of them, or fix the key that was meant to be different."),
    "F622": ("high", "Two starred names in one assignment",
             "An unpacking can have only one starred name. " + _SYNTAX,
             "Star one name only."),
    "F631": ("medium", "`assert` on a tuple",
             "A tuple with something in it is always true, so this assertion can never fail.",
             "Drop the parentheses: `assert condition, message`."),
    "F632": ("medium", "`is` used to compare with a literal",
             "`is` asks whether two values are the same object, not whether they're equal, and for strings and numbers "
             "that's up to the interpreter: this can be false for equal values.",
             "Use `==` or `!=`."),
    "F633": ("high", "`print >>` from Python 2",
             "In Python 3 `print` is a function, and `>>` on it raises `TypeError`.",
             "Write `print(..., file=...)`."),
    "F634": ("medium", "`if` on a tuple",
             "A tuple with something in it is always true, so this branch always runs.",
             "Test the values themselves, joined with `and` or `or`."),
    "F701": ("high", "`break` outside a loop",
             "There's no loop here for `break` to leave. " + _SYNTAX,
             "Remove it, or use `return`."),
    "F702": ("high", "`continue` outside a loop",
             "There's no loop here for `continue` to go on with. " + _SYNTAX,
             "Remove it, or use `return`."),
    "F704": ("high", "`yield` or `await` outside a function",
             "They only work inside a function. " + _SYNTAX,
             "Move the code into a function."),
    "F706": ("high", "`return` outside a function",
             "`return` only works inside a function. " + _SYNTAX,
             "Remove it, or move the code into a function."),
    "F707": ("high", "Bare `except:` before other handlers",
             "A bare `except:` has to be the last handler of its `try`. " + _SYNTAX,
             "Move it below the others."),
    "F811": ("medium", "Defined twice:",
             "An earlier function or class of this name is never used before this one replaces it, so the earlier code can't run.",
             "Rename one of them, or delete the one that isn't wanted."),
    "F821": ("high", "Undefined name:",
             "Nothing in this file defines or imports this name, so Python raises `NameError` when it gets to it "
             "(for a name in an annotation, when the annotation is read).",
             "Define or import it, or fix the spelling."),
    "F822": ("high", "Undefined name in `__all__`:",
             "`__all__` lists a name the module doesn't define, so `from module import *` raises `AttributeError`.",
             "Define it, or take it out of `__all__`."),
    "F823": ("high", "Local variable used before it's assigned:",
             "The function assigns this name further down, which makes it a local variable everywhere in the function, "
             "so reading it here raises `UnboundLocalError` rather than using the outer one.",
             "Declare it `global` or `nonlocal`, or give the local value another name."),
    "F901": ("high", "`raise NotImplemented`",
             "`NotImplemented` is a value for comparison methods to return, not an exception: raising it is a `TypeError`.",
             "Raise `NotImplementedError`."),
    "PLE0100": ("high", "`yield` in `__init__`",
                "A `yield` turns `__init__` into a generator, so its body never runs and creating the object raises `TypeError`.",
                "Take the `yield` out of `__init__`."),
    "PLE0101": ("high", "`__init__` returns a value",
                "`__init__` has to return `None`: with a value, creating the object raises `TypeError`.",
                "Return nothing, or build the object in a classmethod."),
    "PLE0115": ("high", "Name declared `nonlocal` and `global`:",
                "One name can't be both in the same function. " + _SYNTAX,
                "Keep one of the two declarations."),
    "PLE0117": ("high", "`nonlocal` name that no outer function has:",
                "`nonlocal` needs an enclosing function that assigns this name. " + _SYNTAX,
                "Use `global` for a module-level name, or assign it in the outer function."),
    "PLE0118": ("high", "Name used before its `global` declaration:",
                "`global` has to come before the function first uses the name. " + _SYNTAX,
                "Move the `global` line to the top of the function."),
    "PLE0237": ("high", "Attribute that isn't in `__slots__`:",
                "The class has `__slots__`, which lists every attribute its objects can hold, and this one isn't there: "
                "the assignment raises `AttributeError`.",
                "Add it to `__slots__`."),
    "PLE0241": ("high", "Same base class twice:",
                "A class can list each base once: Python raises `TypeError` when it creates this class.",
                "Remove the repeat."),
    "PLE0302": ("high", "Special method with the wrong parameters:",
                "Python calls this method itself, with a fixed number of arguments, so with these parameters that call raises `TypeError`.",
                "Give it the parameters Python passes."),
    "PLE0303": ("high", "`__len__` returns something that isn't a count",
                "`len()` raises unless `__len__` returns a whole number that is zero or more.",
                "Return a non-negative `int`."),
    "PLE0304": ("high", "`__bool__` doesn't return a bool",
                "Testing the object's truth raises `TypeError` unless `__bool__` returns `True` or `False`.",
                "Return a `bool`."),
    "PLE0305": ("high", "`__index__` doesn't return an integer",
                "Using the object as an index raises `TypeError` unless `__index__` returns an `int`.",
                "Return an `int`."),
    "PLE0307": ("high", "`__str__` doesn't return a string",
                "`str()` and `print()` raise `TypeError` unless `__str__` returns a `str`.",
                "Return a `str`."),
    "PLE0308": ("high", "`__bytes__` doesn't return bytes",
                "`bytes()` raises `TypeError` unless `__bytes__` returns `bytes`.",
                "Return `bytes`."),
    "PLE0309": ("high", "`__hash__` doesn't return an integer",
                "`hash()` raises `TypeError` unless `__hash__` returns an `int`, so the object can't be a dict key or go in a set.",
                "Return an `int`."),
    "PLE0643": ("high", "Index past the end",
                "The sequence is shorter than this index, so the lookup raises `IndexError`.",
                "Use an index the sequence has."),
    "PLE1132": ("high", "Keyword argument passed twice:",
                "Python raises `TypeError` when a call gets two values for one keyword (`SyntaxError` when it's written out twice).",
                "Pass it once."),
    "PLE1142": ("high", "`await` outside an `async` function",
                "`await` only works inside `async def`. " + _SYNTAX,
                "Make the function `async def`, or run the coroutine with `asyncio.run`."),
    "PLE1205": ("medium", "More arguments than the log message has placeholders", _LOGGING,
                "Add a placeholder, or drop the extra argument."),
    "PLE1206": ("medium", "Fewer arguments than the log message has placeholders", _LOGGING,
                "Pass a value for each placeholder."),
    "PLE1307": ("high", "`%` format type doesn't fit its value",
                "A numeric conversion such as `%d` is given a value of another type, so the `%` raises `TypeError`.",
                "Use `%s`, or pass a number."),
    "PLE1507": ("high", "`os.getenv` with a name that isn't a string",
                "An environment variable's name is a `str`; any other type raises `TypeError`.",
                "Pass the variable's name as a string."),
    "PLE1520": ("high", "`@singledispatchmethod` on a plain function",
                "`singledispatchmethod` only works on methods: called as a plain function it raises `TypeError`.",
                "Use `functools.singledispatch`."),
    "PLE1700": ("high", "`yield from` in an `async` function",
                "`async def` doesn't allow `yield from`. " + _SYNTAX,
                "Loop with `async for` and `yield` each item."),
    "B015": ("medium", "Comparison whose result is thrown away",
             "The line compares two values and does nothing with the answer: nothing is assigned and nothing is checked.",
             "Use `=` to assign, or `assert` to check."),
    "B030": ("high", "`except` with something that isn't an exception class",
             "When an exception reaches this handler, Python raises `TypeError` instead of matching it.",
             "List exception classes only."),
}
# Names a host puts in builtins, which no file defines: IPython's, and the
# `_` of `gettext.install()`. A project's own Ruff config would list them,
# and that config is never read here.
HOST_NAMES = ("_", "__IPYTHON__", "get_ipython")
_QUOTED = re.compile(r"`([^`\n]{1,80})`")
_DEFINITION = re.compile(r"\s*(?:async\s+def|def|class)\s")
_FIRST_LINE = re.compile(r"from line (\d+)")
_MAKES_NAMES = re.compile(r"\bglobals\(\)\s*(?:\[|\.update\b|\.setdefault\b)|\bsys\.modules\[__name__\]")
_MODULE_GETATTR = re.compile(r"^(?:def\s+__getattr__\b|__getattr__\s*=)", re.MULTILINE)
# How Ruff spots a `# noqa` comment, and its own `# ruff: ignore[...]`.
_NOQA = re.compile(r"#\s*(?:noqa|ruff:\s*ignore)", re.IGNORECASE)


class RuffError(RuntimeError):
    pass


class RuffTimeout(RuffError):
    pass


def available() -> bool:
    return importlib.util.find_spec("ruff") is not None


def scope(path: str) -> frozenset[str]:
    """Ruff reads Python files outside the tests."""
    return frozenset({"ruff"}) if path.endswith(".py") and not is_test_path(path) else frozenset()


def plan(files: list[FileDiff], contents: dict[str, str] | None) -> tuple[list[str], list[str]]:
    """The Python files Ruff reads, and the ones it would read but doesn't:
    past MAX_FILES, longer than MAX_FILE_CHARS, or without their text. The
    order is Bandit's: files the PR edits first, biggest change first, then
    files moved here from the tests."""
    contents = contents or {}
    candidates = [
        file for file in files
        if scope(file.path) and file.status != "deleted" and not file.binary and (file.added() or moved_in(file, scope))
    ]

    def rank(file: FileDiff) -> tuple[bool, int]:
        moved = moved_in(file, scope)
        return moved, -(len(checked_lines(file, contents, scope)) if moved else file.additions)

    readable = [f for f in candidates if f.path in contents and len(contents[f.path]) <= MAX_FILE_CHARS]
    read = [file.path for file in sorted(readable, key=rank)[:MAX_FILES]]
    return read, [file.path for file in candidates if file.path not in read]


def check(files: list[FileDiff], contents: dict[str, str] | None = None) -> list[Finding]:
    if not contents or not available():
        return []
    targets, _ = plan(files, contents)
    if not targets:
        return []
    # A file moved here from the tests counts every line as added.
    added = {file.path: dict(checked_lines(file, contents, scope)) for file in files if file.path in targets}
    findings = []
    for issue in run_ruff({path: contents[path] for path in targets}):
        rule = RULES.get(issue["code"])
        mine = added.get(issue["path"], {})
        on_added = [n for n in range(issue["line"], issue["end_line"] + 1) if n in mine]
        if rule is None or not on_added:
            continue
        quoted = _QUOTED.search(issue["message"])
        name = quoted.group(1) if quoted else ""
        if _left_out(issue, name, contents[issue["path"]]):
            continue
        severity, title, detail, suggestion = rule
        detail += f" Ruff reports this as {issue['code']}."
        if any(_NOQA.search(mine[n]) for n in on_added):
            detail += " The PR marks the line `# noqa`. Stratum reports it anyway, because the PR's author added that comment."
        findings.append(Finding(
            title=f"{title} `{name}`" if title.endswith(":") and name else title.rstrip(":"),
            detail=detail,
            severity=severity,
            category="bug",
            path=issue["path"],
            line_start=on_added[0],
            line_end=on_added[0],
            suggestion=suggestion,
            source="check",
            rule=f"ruff/{issue['code']}",
            confidence=0.9,
        ))
    return findings


def _left_out(issue: dict[str, Any], name: str, text: str) -> bool:
    """What a rule reports that isn't an error, or that Ruff has no way to know."""
    code, lines = issue["code"], text.split("\n")
    if code == "F811":
        # A name imported twice is untidy, not an error. A second `def` or `class` hides the first.
        first = _FIRST_LINE.search(issue["message"])
        both = [lines[n - 1] for n in (int(first.group(1)), issue["line"]) if 0 < n <= len(lines)] if first else []
        return len(both) < 2 or not all(_DEFINITION.match(line) for line in both)
    if code in ("F821", "F822") and _MAKES_NAMES.search(text):
        return True  # a file that writes to `globals()`, or sets attributes on its own module, can define any name
    if code == "F821":
        # In a package's `__init__.py`, `from ._api import x` binds `_api` too.
        return basename(issue["path"]) == "__init__.py" and bool(name) and re.search(rf"^\s*from\s+\.{re.escape(name)}[\s.]", text, re.MULTILINE) is not None
    if code == "F822":
        # A module's own `__getattr__` can supply any name `__all__` lists.
        return _MODULE_GETATTR.search(text) is not None
    if code == "PLE0101":
        # `return super().__init__()` returns None, as `__init__` must.
        return any("__init__(" in line for line in lines[issue["line"] - 1:issue["end_line"]])
    return False


def run_ruff(sources: dict[str, str], *, rules: Iterable[str] = tuple(RULES), timeout: float = TIMEOUT_SECONDS) -> list[dict[str, Any]]:
    """Ruff's issues for each file's text, as plain dicts keyed by our path.
    Each file goes in its own numbered folder of a temporary one, named
    `__init__.py` if that's what it is (a package's `__init__.py` may use
    `__path__`) and `module.py` otherwise, and Ruff runs once over all of
    them. `rules` is for the benchmark, which also tries rules left out of
    RULES. Raises RuffError when Ruff doesn't finish or can't be read."""
    with tempfile.TemporaryDirectory(prefix="stratum-ruff-", ignore_cleanup_errors=True) as folder:
        paths: dict[str, str] = {}
        names = []
        for index, (path, text) in enumerate(sources.items()):
            os.mkdir(os.path.join(folder, str(index)))
            names.append(os.path.join(str(index), "__init__.py" if basename(path) == "__init__.py" else "module.py"))
            # newline="" writes the line endings as they are, keeping line numbers.
            with open(os.path.join(folder, names[-1]), "w", encoding="utf-8", errors="replace", newline="") as handle:
                handle.write(text)
            paths[str(index)] = path
        command = [
            # -P and the temporary folder as the working one: `-m` would otherwise look for `ruff` in the
            # folder the review runs from first, which can be a checkout of the PR.
            sys.executable, "-P", "-m", "ruff", "check", "--isolated", "--no-cache", "--no-fix", "--ignore-noqa", "--exit-zero",
            "--select", ",".join(rules), "--target-version", TARGET, "--config", f"builtins = {json.dumps(list(HOST_NAMES))}",
            "--output-format", "json", *names,
        ]
        # Ruff's own environment variables (an output file, a cache folder) belong to whoever set them, not to this run.
        env = {key: value for key, value in os.environ.items() if not key.upper().startswith("RUFF_")}
        try:
            result = subprocess.run(command, capture_output=True, text=True, encoding="utf-8", errors="replace", timeout=timeout, cwd=folder, env=env)
        except subprocess.TimeoutExpired:
            raise RuffTimeout(f"Ruff took longer than {timeout:.0f} s, so its findings are missing from this review.") from None
    try:
        report = json.loads(result.stdout) if result.returncode == 0 else None
    except ValueError:
        report = None
    if not isinstance(report, list):
        raise RuffError(f"Ruff stopped with exit code {result.returncode} and no report. It has to be version {MIN_VERSION} or newer.")
    issues = []
    for issue in report:
        if not isinstance(issue, dict):
            continue
        path = paths.get(os.path.basename(os.path.dirname(str(issue.get("filename") or ""))))
        line = (issue.get("location") or {}).get("row")
        end = issue.get("end_location") or {}
        if path is None or not isinstance(line, int) or not issue.get("code"):
            continue
        last = end.get("row") if isinstance(end.get("row"), int) else line
        if end.get("column") == 1:  # it ends where the next line starts, so not on that line
            last -= 1
        issues.append({
            "path": path,
            "code": str(issue["code"]),
            "message": str(issue.get("message") or ""),
            "line": line,
            "end_line": max(line, last),
        })
    return issues
