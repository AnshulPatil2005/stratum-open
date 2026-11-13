"""Tests a PR weakens, found on the line: a test that was running and is now
switched off, assertions removed from a test with nothing put in their place,
and an assertion whose tolerance got wider.

The quickest way to make a failing test pass is to stop it checking, and one
`@pytest.mark.skip` is easy to miss in a long diff. These rules read only the
diff, removed lines as well as added ones, in files the checks take for tests
(Rust's `#[ignore]` anywhere, since its tests live beside the code).

Each rule reports only what the diff proves, so a quiet run proves little:

- a skip is reported when nothing decides it: `skipif(...)`, a skip inside an
  `if` or an `except`, `#[cfg_attr(miri, ignore)]` and the like are how a test
  is kept off a platform it can't run on, and are left alone;
- a test the PR itself adds, switched off from the start, is left alone too:
  nothing that ran before stops running;
- an assertion counts as removed only when the lines removed with it have no
  code written in their place, and the function it was in gains no assertion
  anywhere the diff shows: a rewritten check, or one moved into a helper, is
  not a finding;
- a function is what the diff shows of it: its lines by their indentation,
  below its first line or below a hunk header that names it. Where a hunk
  doesn't say which function it starts in, assertions added in the hunk
  before, under the same header, count as possibly replacing the removed ones.
"""

from __future__ import annotations

import re
from collections import Counter
from dataclasses import dataclass
from functools import cached_property

from stratum_open.checks.common import extension, is_test_path
from stratum_open.checks.patterns import JAVASCRIPT, MAX_LINE
from stratum_open.diff import FileDiff, Hunk, Line
from stratum_open.findings import Finding

SKIPPED = "tests/skipped"
ONLY = "tests/only"
ASSERTIONS = "tests/assertions-removed"
TOLERANCE = "tests/tolerance-loosened"
DELETED = "tests/deleted"

_LANGUAGES = {".py": "python", ".go": "go", ".rs": "rust"} | {ext: "javascript" for ext in JAVASCRIPT}
_SLASHES = re.compile(r"^\s*(?://|/\*|\*)")
_COMMENT = {"python": re.compile(r"^\s*#"), "javascript": _SLASHES, "go": _SLASHES, "rust": _SLASHES}

# Where a function starts, with its name. In JavaScript a test is a call
# with its title, and hooks and helpers are functions too.
_FUNCTION = {
    "python": re.compile(r"^\s*(?:async\s+)?def\s+(?P<name>\w+)"),
    "javascript": re.compile(
        r"""^\s*(?:await\s+)?(?:
            (?P<test>(?:\w+\.)?(?:it|test|specify|xit|xtest|fit)(?:\.(?!describe)\w+)*)\s*\(\s*(?:(?P<q>["'`])(?P<title>.*?)(?P=q))?
            | (?P<hook>(?:before|after)(?:Each|All)?)\s*\(
            | (?:export\s+)?(?:default\s+)?(?:async\s+)?function\b\s*\*?\s*(?P<function>\w*)
            | (?:export\s+)?(?:const|let|var)\s+(?P<arrow>\w+)\s*=\s*(?:async\s*)?(?:function\b|\(.*\)\s*(?::[^=]+)?=>|\w+\s*=>)
        )""", re.X),
    "go": re.compile(r"^func\s+(?:\([^)]*\)\s*)?(?P<name>\w+)"),
    "rust": re.compile(r"""^\s*(?:pub(?:\([^)]*\))?\s+)?(?:(?:async|const|unsafe|extern\s+"[^"]*")\s+)*fn\s+(?P<name>\w+)"""),
}
_GO_TEST = re.compile(r"^(?:Test|Benchmark|Fuzz|Example)")
_RUST_TEST_ATTRIBUTE = re.compile(r"^\s*#\[\s*(?:\w+::)*\w*test\w*")
_ATTRIBUTE = {"python": re.compile(r"^\s*@"), "rust": re.compile(r"^\s*#\[")}
_PY_CLASS = re.compile(r"^\s*class\s+(?P<name>\w+)")
_CLOSER = re.compile(r"^\s*[)\]}]")

_ASSERTION = {
    "python": re.compile(r"^\s*assert\b|\bassert\w*\s*\(|\b(?:raises|warns|deprecated_call)\s*\("),
    "javascript": re.compile(
        r"\bexpect\w*\s*(?:[.(]|$)|\.expect\(|\bassert(?:\.\w+)*\s*\(|\.should\b"
        r"|\bt\.(?:plan|equals?|strictEquals?|notEqual|deepEqual|same|strictSame|notSame|ok|notOk|is|not|throws|doesNotThrow|rejects|resolves|match|error|ifError|pass|fail)\("
    ),
    "go": re.compile(r"\b(?:t|b|tb|f)\.(?:Fatal|Error|Fail)(?:f|Now)?\(|\b(?:assert|require)\.\w+\(|\bAssert\w*\(|\bExpect\("),
    "rust": re.compile(r"\bassert\w*!|\.assert\w*\("),
}


def check(files: list[FileDiff]) -> list[Finding]:
    findings: list[Finding] = []
    readings: list[_Reading] = []
    for file in files:
        language = _LANGUAGES.get(extension(file.path))
        if language is None or file.binary or file.status == "deleted" or not file.hunks:
            continue
        test = is_test_path(file.path)
        if not (test or language == "rust"):
            continue
        read = _Reading(file, language)
        findings.extend(_switched_off(read))
        if test:
            findings.extend(_assertions_removed(read))
            findings.extend(_tolerance_loosened(read))
            readings.append(read)
    # A test deleted counts only beside a change to the code it tested, and not when it moved to another file.
    if any(not is_test_path(file.path) and extension(file.path) in _LANGUAGES and file.hunks for file in files):
        moved = {name for read in readings for name, (_, _, function) in read.functions("+").items() if function.test}
        for read in readings:
            findings.extend(_deleted(read, moved))
    return findings


def _finding(file: FileDiff, number: int | None, rule: str, title: str, detail: str, suggestion: str) -> Finding:
    return Finding(
        title=title, detail=detail, severity="medium", category="tests", path=file.path, line_start=number, line_end=number,
        suggestion=suggestion, source="check", rule=rule, confidence=0.8,
    )


# ── what a diff shows of a file ──────────────────────────────────────────


@dataclass(eq=False)
class _Function:
    key: object  # what its lines are counted under: its name, or where in the diff it is when it can't be named
    name: str = ""  # "" when the diff doesn't say for sure which function this is
    first: Line | None = None  # its first line, when a hunk shows it
    test: bool = False


@dataclass(eq=False)
class _Run:
    """Added and removed lines with nothing unchanged between them."""
    start: int
    end: int = 0  # the index after its last line
    removes: bool = False
    added: Line | None = None  # the first line it adds
    code: bool = False  # whether it adds a line of code (not a comment, a blank line or `pass`)


class _Reading:
    """One file's hunks with the function each line is in, on each side: a
    removed line is in a function of the old file, an added line in one of
    the new file, and an unchanged line in both, which differ when the PR
    renames or adds a function around it."""

    def __init__(self, file: FileDiff, language: str) -> None:
        self.file = file
        self.language = language
        self.old, _ = self._side("-")
        # opener: for each line of the new file, the nearest line above that is less indented (None when the hunk doesn't show it).
        self.new, self.opener = self._side("+")
        self.runs = [self._runs(hunk) for hunk in file.hunks]

    def code(self, line: Line) -> bool:
        """Whether a line is code someone wrote: not blank, not a comment, and
        not long enough to be a minified bundle among the fixtures."""
        return bool(line.text.strip()) and len(line.text) <= MAX_LINE and not _COMMENT[self.language].match(line.text)

    def assertion(self, line: Line) -> bool:
        return self.code(line) and bool(_ASSERTION[self.language].search(line.text)) and not _FUNCTION[self.language].match(line.text)

    def function_at(self, text: str, before: list[str]) -> tuple[str, bool] | None:
        """(name, whether it's a test) of the function `text` starts.
        `before`: the lines above it on its side, for Rust's attributes."""
        match = _FUNCTION[self.language].match(text)
        if not match:
            return None
        if self.language == "javascript":
            groups = match.groupdict()
            if groups["test"]:
                return groups["title"] or "", True
            return groups["hook"] or groups["function"] or groups["arrow"] or "", False
        name = match.group("name")
        if self.language == "python":
            return name, name.startswith("test")
        if self.language == "go":
            return name, bool(_GO_TEST.match(name))
        marked = False
        for earlier in reversed(before):
            if not _ATTRIBUTE["rust"].match(earlier):
                break
            marked = marked or bool(_RUST_TEST_ATTRIBUTE.match(earlier))
        return name, marked

    def _side(self, kind: str) -> tuple[list[list[_Function | None]], list[list[Line | None]]]:
        rows: list[list[_Function | None]] = []
        openers: list[list[Line | None]] = []
        trailing: _Function | None = None
        header = None
        for number, hunk in enumerate(self.file.hunks):
            leading = current = self._leading(hunk, number, trailing if hunk.header == header else None)
            # The lines that open the blocks the current line is in, shallowest first, each with the function it starts.
            blocks: list[tuple[int, Line, _Function | None]] = []
            row: list[_Function | None] = []
            opener: list[Line | None] = []
            seen: list[str] = []
            for index, line in enumerate(hunk.lines):
                if line.kind not in (" ", kind):
                    row.append(None)
                    opener.append(None)
                    continue
                above = None
                if self.code(line):
                    indent = _indent(line.text)
                    closes = bool(_CLOSER.match(line.text))
                    # A line ends every block as deep as it is. A closing bracket ends only the deeper ones: it's
                    # the end of the statement at its own depth, and the `):` of a `def` doesn't end the function.
                    while blocks and (blocks[-1][0] > indent or (blocks[-1][0] == indent and not closes)):
                        blocks.pop()
                    above = next((opening for depth, opening, _ in reversed(blocks) if depth < indent), None)
                    if not closes:
                        found = self.function_at(line.text, seen)
                        blocks.append((indent, line, _Function(found[0] or (number, index), found[0], line, found[1]) if found else None))
                    current = _innermost(blocks) or leading
                row.append(current)
                opener.append(above)
                seen.append(line.text)
            rows.append(row)
            openers.append(opener)
            trailing, header = current, hunk.header
        return rows, openers

    def _leading(self, hunk: Hunk, number: int, inherited: _Function | None) -> _Function:
        """The function a hunk starts in, whose first line is above it. The
        hunk's header has the nearest function above. In Python that can be
        one nested in the test (a method of a class the test defines), so
        only a test's own name is taken from it. Otherwise the hunk may carry
        on in the function the one before ended in, when both have the same
        header."""
        found = self.function_at(hunk.header, [])
        if found and found[0] and (found[1] or self.language != "python"):
            return _Function(found[0], found[0], None, found[1])
        if inherited is not None:
            return _Function(inherited.key, "", inherited.first, inherited.test)
        return _Function((number,))

    def _runs(self, hunk: Hunk) -> list[_Run | None]:
        """For each line of a hunk, the run of changed lines it's part of (None for an unchanged line)."""
        runs: list[_Run | None] = []
        run: _Run | None = None
        for index, line in enumerate(hunk.lines):
            if line.kind == " ":
                run = None
            else:
                run = run or _Run(index)
                run.end = index + 1
                if line.kind == "-":
                    run.removes = True
                else:
                    run.added = run.added or line
                    run.code = run.code or (self.code(line) and line.text.strip() != "pass")
            runs.append(run)
        return runs

    def functions(self, kind: str) -> dict[str, tuple[Hunk, int, _Function]]:
        """The named functions whose first line is of this kind ("+" added, "-" removed, " " unchanged), and where."""
        return self._functions[kind]

    @cached_property
    def _functions(self) -> dict[str, dict[str, tuple[Hunk, int, _Function]]]:
        found: dict[str, dict[str, tuple[Hunk, int, _Function]]] = {"+": {}, "-": {}, " ": {}}
        for side in (self.old, self.new):
            for hunk, row in zip(self.file.hunks, side):
                for index, function in enumerate(row):
                    if function is not None and function.name and function.first is hunk.lines[index]:
                        found[hunk.lines[index].kind][function.name] = (hunk, index, function)
        return found

    @cached_property
    def removed_titles(self) -> set[str]:
        """The titles of the JavaScript tests and suites whose first line the PR removes."""
        titled = (_JS_TITLED.match(line.text) for hunk in self.file.hunks for line in hunk.lines if line.kind == "-")
        return {match.group("title") for match in titled if match}


def _innermost(blocks: list[tuple[int, Line, _Function | None]]) -> _Function | None:
    """The function a line counts under: the test it's in, or the outermost
    function when it's in a helper, so a function defined inside one isn't
    counted apart from it."""
    functions = [function for _, _, function in blocks if function is not None]
    return next((function for function in reversed(functions) if function.test), functions[0] if functions else None)


def _indent(text: str) -> int:
    return len(text) - len(text.lstrip())


def _anchor(hunk: Hunk, run: _Run) -> int | None:
    """A new-file line for a finding about lines a run removes: the first
    line added in their place, else the nearest unchanged line with text on
    it (above first), which GitHub also takes a comment on."""
    if run.added is not None:
        return run.added.new
    around = [line for line in hunk.lines[max(0, run.start - 3):run.start][::-1] + hunk.lines[run.end:run.end + 3] if line.new is not None]
    return next((line.new for line in around if line.text.strip()), around[0].new if around else None)


def _quote(text: str, limit: int = 120) -> str:
    text = " ".join(text.split()).replace("`", "'")
    return text if len(text) <= limit else text[:limit - 1] + "…"


# ── a test switched off ──────────────────────────────────────────────────

# pytest's and unittest's markers, as a decorator, in `marks=` or as `pytestmark`.
_PY_MARK = re.compile(r"(?:^\s*@|\bmark\.|\bunittest\.)(?:\w+\.)*(?P<mark>skip|skipif|xfail|expectedFailure)\b(?P<call>\s*\()?")
_PY_UNDECIDED = re.compile(r"\s*(?:\)|True\b|(?:reason|strict|raises|run)\s*=|$)")  # a marker's arguments when no condition is among them
_PY_STRING = re.compile(r"""\s*[rRbBuUfF]{0,2}["']""")
_PY_SKIP_CALL = re.compile(r"^\s*(?:(?:pytest|self)\.(?:skip|skipTest|xfail)\s*\(|raise\s+(?:unittest\.)?SkipTest\b)")
_JS_SKIP = re.compile(
    r"""^\s*(?:await\s+)?(?:
        (?P<call>(?:\w+\.)?(?:it|test|describe|context|suite|specify)\.(?P<mark>skip|only|todo)(?:\.each)?)
        | (?P<prefix>xit|xtest|xspecify|xdescribe|xcontext|fit|fdescribe)
    )\s*\(\s*(?:(?P<q>["'`])(?P<title>.*?)(?P=q))?""", re.X)
_JS_TITLED = re.compile(r"""^\s*(?:await\s+)?(?:\w+\.)?(?:it|test|describe|context|suite|specify)(?:\.each\b.*?\))?\s*\(\s*(?P<q>["'`])(?P<title>.*?)(?P=q)""")
_JS_CALLBACK = re.compile(r"\s*,\s*(?:async\b|function\b|\(|\w+\s*=>)|\s*,?\s*$")  # after a title: the test's function, or the end of the line
_GO_SKIP = re.compile(r"^\s*(?:t|b|tb|f)\.Skip(?:Now|f)?\(")
_GO_SUBTEST = re.compile(r"\.Run\(.*\bfunc\(\w+ \*testing\.\w+\) \{\s*$")  # t.Run("name", func(t *testing.T) {
_RUST_IGNORE = re.compile(r"^\s*#\[ignore\b")
_EXPECTED_FAILURE = re.compile(r"\bxfail\b|\bexpectedFailure\b")
_MARKER_REACH = 200  # lines looked through below a marker for the function it's on: the other markers and their arguments


def _switched_off(read: _Reading) -> list[Finding]:
    findings = []
    for number, hunk in enumerate(read.file.hunks):
        for index, line in enumerate(hunk.lines):
            if line.kind != "+" or not read.code(line):
                continue
            found = _SWITCHES[read.language](read, number, index)
            if found is None:
                continue
            rule, name = found
            marker = _quote(line.text)
            if rule == ONLY:
                findings.append(_finding(
                    read.file, line.new, ONLY, "`.only` leaves the file's other tests out",
                    f"Line {line.new} adds `{marker}`. With it, the test runner runs this one and none of the other tests in the file, "
                    "and still reports success.",
                    "Remove it before merging: it's for running one test while working on it.",
                ))
                continue
            allowed = bool(_EXPECTED_FAILURE.search(line.text))  # the test still runs, and its failure no longer counts
            what = f"`{_quote(name, 60)}`" if name else "A test"
            findings.append(_finding(
                read.file, line.new, SKIPPED, f"{what} is {'allowed to fail' if allowed else 'switched off'}",
                f"Line {line.new} adds `{marker}`, and nothing on it decides when it applies. {what if name else 'A test that ran before this PR'} "
                f"{'can now fail without failing the run' if allowed else 'no longer runs anywhere'}. A test that fails says something about the change.",
                "Fix what the test catches, or say in the PR why it's switched off and what will switch it back on.",
            ))
    return findings


def _python_switch(read: _Reading, number: int, index: int) -> tuple[str, str] | None:
    hunk = read.file.hunks[number]
    text = hunk.lines[index].text
    mark = _PY_MARK.search(text)
    if mark and not _PY_STRING.match(text):
        rest = text[mark.end():]
        if mark.group("call") and not rest.strip():  # the arguments start on the next line
            rest = next((line.text for line in hunk.lines[index + 1:index + 3] if line.kind != "-"), "")
        kind = mark.group("mark")
        decided = bool(mark.group("call")) and not _PY_UNDECIDED.match(rest) and not (kind == "skip" and _PY_STRING.match(rest))
        if kind == "skipif":
            decided = not re.match(r"\s*True\s*[,)]", rest)
        if decided:
            return None
        if not _ATTRIBUTE["python"].match(text):
            # In `marks=` or `pytestmark`: on a line the PR rewrote, or in a file that was there, tests that ran stop running.
            rewritten = read.runs[number][index].removes or (text.startswith("pytestmark") and read.file.status != "added")
            return (SKIPPED, "") if rewritten else None
        return _decorated(read, hunk, index)
    if _PY_SKIP_CALL.match(text):
        return _in_body(read, number, index)
    return None


def _javascript_switch(read: _Reading, number: int, index: int) -> tuple[str, str] | None:
    text = read.file.hunks[number].lines[index].text
    match = _JS_SKIP.match(text)
    if not match:
        return None
    mark, title = match.group("mark") or match.group("prefix"), match.group("title") or ""
    if not match.group("q") and not (match.group("call") or "").endswith(".each"):
        return None  # `test.skip(isWindows, ...)`: a condition, not a title
    if mark == "fit" and not _JS_CALLBACK.match(text, match.end()):
        return None  # `fit('linear', xs, ys)`: someone's own function, not Jasmine's focused test
    if mark in ("only", "fit", "fdescribe"):
        return ONLY, title
    # Only a test that ran under this title before: the PR removes its `it("title"` line.
    return (SKIPPED, title) if title in read.removed_titles else None


def _go_switch(read: _Reading, number: int, index: int) -> tuple[str, str] | None:
    return _in_body(read, number, index) if _GO_SKIP.match(read.file.hunks[number].lines[index].text) else None


def _rust_switch(read: _Reading, number: int, index: int) -> tuple[str, str] | None:
    hunk = read.file.hunks[number]
    return _decorated(read, hunk, index) if _RUST_IGNORE.match(hunk.lines[index].text) else None


_SWITCHES = {"python": _python_switch, "javascript": _javascript_switch, "go": _go_switch, "rust": _rust_switch}


def _was_there(read: _Reading, first: Line, name: str) -> bool:
    """Whether the function starting at `first` ran before the PR: its first
    line is unchanged, or the PR removes a line that started one of that name."""
    return first.kind == " " or name in read.functions("-")


def _decorated(read: _Reading, hunk: Hunk, index: int) -> tuple[str, str] | None:
    """The function (or Python class) under the marker at `index`, if it was there before."""
    attribute, indent = _ATTRIBUTE[read.language], _indent(hunk.lines[index].text)
    for line in hunk.lines[index + 1:index + _MARKER_REACH]:
        if line.kind == "-" or not line.text.strip() or _COMMENT[read.language].match(line.text) or attribute.match(line.text):
            continue
        if _indent(line.text) > indent or _CLOSER.match(line.text):
            continue  # the rest of a marker written over several lines
        found = read.function_at(line.text, [])
        if found:
            return (SKIPPED, found[0]) if _was_there(read, line, found[0]) else None
        held = _PY_CLASS.match(line.text) if read.language == "python" else None
        return (SKIPPED, held.group("name")) if held and line.kind == " " else None
    return None


def _in_body(read: _Reading, number: int, index: int) -> tuple[str, str] | None:
    """A skip called at `index`, when it's a statement of the function's own
    body and the function was there before. Under anything else (an `if`,
    an `except`, a loop) something decides whether it runs."""
    hunk, above = read.file.hunks[number], read.opener[number][index]
    if above is None:
        # The hunk starts below the function's first line. A Go function starts at the margin, so one tab in is its own body.
        found = read.function_at(hunk.header, [])
        return (SKIPPED, found[0]) if found and found[0] and read.language == "go" and _indent(hunk.lines[index].text) == 1 else None
    found = read.function_at(above.text, [])
    if found:
        return (SKIPPED, found[0]) if _was_there(read, above, found[0]) else None
    if read.language == "go" and _GO_SUBTEST.search(above.text):
        return (SKIPPED, "") if above.kind == " " else None
    return None


# ── assertions removed ───────────────────────────────────────────────────


def _assertions_removed(read: _Reading) -> list[Finding]:
    gained: Counter[object] = Counter()  # assertion lines added, by the function they're in
    added_text = set()
    for hunk, row in zip(read.file.hunks, read.new):
        for line, function in zip(hunk.lines, row):
            if line.kind == "+":
                added_text.add(" ".join(line.text.split()))
                if function is not None and read.assertion(line):
                    gained[function.key] += 1
    rewritten = read.functions("+")
    lost: dict[object, list[tuple[Hunk, int, _Function, _Run]]] = {}
    for hunk, row, runs in zip(read.file.hunks, read.old, read.runs):
        for index, (line, function, run) in enumerate(zip(hunk.lines, row, runs)):
            if line.kind != "-" or function is None or not read.assertion(line):
                continue
            if function.first is not None and function.first.kind == "-" and function.key not in rewritten:
                continue  # removed with its function, or the function was renamed: not the same test still running
            if run.code or " ".join(line.text.split()) in added_text:
                continue  # code written where it stood may check the same thing another way (a helper), or the line only moved
            lost.setdefault(function.key, []).append((hunk, index, function, run))
    findings = []
    for key, removed in lost.items():
        if gained[key]:
            continue
        hunk, index, function, run = removed[0]
        count, first = len(removed), hunk.lines[index].text
        where = f"`{_quote(function.name, 60)}`" if function.name else "a test"
        more = f" and {count - 1} more" if count > 1 else ""
        findings.append(_finding(
            read.file, _anchor(hunk, run), ASSERTIONS,
            f"Assertions removed from {where}" if function.name else f"Assertion removed: `{_quote(first, 60)}`",
            f"This PR removes {'an assertion' if count == 1 else f'{count} assertions'} from {where} and puts nothing in "
            f"{'its' if count == 1 else 'their'} place: `{_quote(first)}`{more}. The test still runs and passes, but it no longer checks this.",
            "Put the assertion back, or say in the PR why what it checked no longer holds.",
        ))
    return findings


# ── a tolerance loosened ─────────────────────────────────────────────────

_NUMBER = r"[-+]?(?:\d[\d_]*\.?[\d_]*|\.\d[\d_]*)(?:[eE][-+]?\d+)?"
# A tolerance given by name, and the last argument of the calls that take it by place.
_TOLERANCES = (
    (re.compile(rf"\b(?P<name>abs|rel|delta|rel_tol|abs_tol|rtol|atol|epsilon|max_relative)\s*=\s*(?P<value>{_NUMBER})"), 1),
    (re.compile(rf"\b(?P<name>places|decimal)\s*=\s*(?P<value>{_NUMBER})"), -1),
    (re.compile(r"\.(?P<name>toBeCloseTo)\([^()]*(?:\([^()]*\)[^()]*)*,\s*(?P<value>\d+)\s*\)"), -1),
    (re.compile(rf"\b(?P<name>InDelta|InEpsilon)\(.*,\s*(?P<value>{_NUMBER})\s*[,)]"), 1),
)
_APPROXIMATE = re.compile(r"\b(?:approx|isclose|allclose)\s*\(")


def _tolerances(text: str) -> tuple[str, dict[str, float]]:
    """The line with each tolerance's number blanked, and the tolerances by
    name, signed so that a bigger number is always a wider tolerance."""
    values: dict[str, float] = {}
    for pattern, sign in _TOLERANCES:
        for match in pattern.finditer(text):
            try:
                values[match.group("name")] = sign * float(match.group("value").replace("_", ""))
            except ValueError:
                continue
        text = pattern.sub(lambda m: m.group(0)[:m.start("value") - m.start()] + "\0" + m.group(0)[m.end("value") - m.start():], text)
    return " ".join(text.split()), values


def _compares(read: _Reading, line: Line) -> bool:
    """Whether a line is an assertion or an approximate comparison: `rtol=`
    and `epsilon=` are also arguments of solvers and optimizers under test."""
    return read.assertion(line) or (read.code(line) and bool(_APPROXIMATE.search(line.text)))


def _tolerance_loosened(read: _Reading) -> list[Finding]:
    findings = []
    for hunk in read.file.hunks:
        # The removed lines that carry a tolerance, by their text without its number.
        before: dict[str, tuple[dict[str, float], Line]] = {}
        for line in hunk.lines:
            if line.kind == "-" and _compares(read, line):
                shape, was = _tolerances(line.text)
                if was:
                    before.setdefault(shape, (was, line))
        for line in hunk.lines:
            if line.kind != "+" or not before or not _compares(read, line):
                continue
            shape, now = _tolerances(line.text)
            was, old = before.get(shape, ({}, line))
            if now and now.keys() == was.keys() and now != was and all(now[name] >= was[name] for name in now):
                findings.append(_finding(
                    read.file, line.new, TOLERANCE, "An assertion's tolerance is wider",
                    f"Line {line.new} is the same assertion as before with a wider tolerance: `{_quote(old.text)}` became "
                    f"`{_quote(line.text)}`. A result that failed the old one can pass this one.",
                    "Keep the old tolerance and fix what moved the result, or say in the PR why the result may now differ by more.",
                ))
    return findings


# ── a test deleted ───────────────────────────────────────────────────────


def _deleted(read: _Reading, moved: set[str]) -> list[Finding]:
    if any(function is not None and function.test and function.first is line and line.kind == "+"
           for hunk, row in zip(read.file.hunks, read.new) for line, function in zip(hunk.lines, row)):
        return []  # the file gains a test: more likely rewritten or renamed than dropped
    kept = read.functions(" ")
    gone = {name: where for name, where in read.functions("-").items() if where[2].test and name not in kept and name not in moved}
    if not gone:
        return []
    names = list(gone)
    hunk, index, _ = gone[names[0]]
    run = read.runs[read.file.hunks.index(hunk)][index]
    listed = ", ".join(f"`{_quote(name, 60)}`" for name in names[:5]) + (f" and {len(names) - 5} more" if len(names) > 5 else "")
    return [_finding(
        read.file, _anchor(hunk, run), DELETED, f"`{_quote(names[0], 60)}` is deleted" if len(names) == 1 else f"{len(names)} tests are deleted",
        f"This PR deletes {listed} from this file, adds no test to it, and changes code outside the tests.",
        "Say in the PR what the deleted tests covered and why that no longer needs a test.",
    )]
