import difflib
import json

from fakes import FakeGitHub, FakeModel

from stratum_open.diff import AROUND_LINES, FUNCTION_LINES, parse_diff, render, with_context, with_function
from stratum_open.policy import parse_policy
from stratum_open.profiles import apply
from stratum_open.review import review
from stratum_open.runner import Context, review_pull

MODULE = '''import os

LIMIT = 10


@cache
@retry(times=3)
def load(path, *, strict=False):
    """Read a file."""
    if strict:
        check(path)
    data = read(path)
    return data


class Store:
    size = 0

    async def put(self, key, value):
        self.items[key] = value
        self.size += 1

    def each(self, visit):
        count = 0

        def one(key):
            value = self.items[key]
            if value is None:
                return
            visit(key, value)
            log(key)

        for key in self.items:
            one(key)
        return self.size


def last():
    return LIMIT
'''
# Line 1 is the def, 2 the docstring, step_N is line N + 2, and the return is line 203.
LONG = 'def long(a):\n    """Add up."""\n' + "".join(f"    step_{n} = {n}\n" for n in range(1, 201)) + "    return a\n"


def between(old: str, new: str, path: str = "m.py") -> str:
    hunks = list(difflib.unified_diff(old.split("\n")[:-1], new.split("\n")[:-1], lineterm=""))[2:]
    return "\n".join([f"diff --git a/{path} b/{path}", f"--- a/{path}", f"+++ b/{path}", *hunks, ""])


def adding(text: str, *added: str, path: str = "m.py"):
    """The diff that adds the lines of `text` that read `added`."""
    old = "".join(line + "\n" for line in text.split("\n")[:-1] if line.strip() not in added)
    [file] = parse_diff(between(old, text, path))
    return file


def shown(file):
    return [[line.new for line in hunk.lines] for hunk in file.hunks]


def test_a_hunk_is_shown_with_its_function_from_the_decorators_down():
    file = adding(MODULE, "check(path)")
    assert shown(file) == [[8, 9, 10, 11, 12, 13, 14]]
    wide = with_function(file, MODULE)
    assert shown(wide) == [[6, 7, 8, 9, 10, 11, 12, 13, 14]]  # the function ends at 13: nothing more after the hunk's own lines
    assert [(line.kind, line.old, line.new) for line in wide.hunks[0].lines][:2] == [(" ", 6, 6), (" ", 7, 7)]
    assert (wide.hunks[0].old_start, wide.hunks[0].new_start) == (6, 6)
    rows = render(wide).split("\n")
    assert "     6    @cache" in rows and "     8    def load(path, *, strict=False):" in rows
    assert [row for row in rows if row[7:8] == "+"] == ["    11 +          check(path)"]  # still the only line marked as added
    assert wide.added() == file.added() and file.hunks[0].lines[0].new == 8  # a copy: the diff itself is as it was


def test_the_innermost_function_is_the_one_shown():
    assert shown(with_function(adding(MODULE, "log(key)"), MODULE)) == [[26, 27, 28, 29, 30, 31, 32, 33, 34]]  # from `def one`, not `def each`


def test_a_method_is_shown_without_the_rest_of_its_class():
    wide = with_function(adding(MODULE, "for key in self.items:"), MODULE)
    assert shown(wide) == [list(range(23, 37))]  # `def each` to its return, the function inside it too
    assert "class Store:" not in render(wide) and "async def put" not in render(wide)


def test_a_hunk_across_two_functions_gets_both():
    file = adding(MODULE, "self.size += 1", "count = 0")
    assert shown(file) == [list(range(18, 28))]  # one hunk: the end of `put` and the start of `each`
    assert shown(with_function(file, MODULE)) == [list(range(18, 36))]


def test_code_outside_a_function_gets_lines_around_it():
    for added in ("LIMIT = 10", "size = 0"):  # at module level, and in a class body
        file = adding(MODULE, added)
        assert with_function(file, MODULE).hunks == with_context(file, MODULE, AROUND_LINES).hunks
        assert with_function(file, MODULE, 2).hunks == with_context(file, MODULE, 2).hunks  # review.context_lines, when it's set
    assert shown(with_function(adding(MODULE, "LIMIT = 10"), MODULE)) == [list(range(1, 12))]


def test_a_file_with_no_boundaries_gets_lines_around_each_hunk():
    broken = MODULE + "def broken(:\n"
    too_deep = MODULE + "x = " + "-" * 10000 + "1\n"  # the parser gives up on it
    old_mac = MODULE.replace("LIMIT = 10\n", "LIMIT = 10\r# one line to git, two to Python\n")
    for text, path in ((broken, "m.py"), (too_deep, "m.py"), (old_mac, "m.py"), (MODULE, "m.ts")):
        file = adding(text, "check(path)", path=path)
        assert with_function(file, text).hunks == with_context(file, text, AROUND_LINES).hunks
        assert with_function(file, text, 3).hunks == with_context(file, text, 3).hunks
    file = adding(MODULE, "check(path)")
    assert with_function(file, None) is file and with_function(file, "stale = True\n") is file
    assert shown(with_function(file, chr(0xFEFF) + MODULE)) == [list(range(6, 15))]  # a byte order mark is still Python


def test_a_hunk_that_only_removes_lines_stays_a_note():
    new = MODULE.replace("        check(path)\n", "")
    [file] = parse_diff(between(MODULE, new))
    assert with_function(file, new).hunks == file.hunks and "(1 line removed after line 10)" in render(with_function(file, new))


def test_a_removal_higher_up_does_not_cut_the_function_off():
    old = MODULE.replace("        count = 0\n", "        count = 0\n        total = 0\n").replace("        return self.size\n", "")
    [file] = parse_diff(between(old, MODULE))
    assert shown(file) == [[22, 23, 24, None, 25, 26, 27], [32, 33, 34, 35, 36, 37, 38]]  # a hunk of its own: a note, when rendered
    [hunk] = with_function(file, MODULE).hunks
    assert [line.new for line in hunk.lines] == [23, 24, None, *range(25, 39)]  # `def each` down, the note where the line was
    assert hunk.lines[2].text == "(1 line removed after line 24)" and render(with_function(file, MODULE)).count("@@") == 1
    assert [(line.old, line.new) for line in hunk.lines if line.new in (24, 25)] == [(24, 24), (26, 25)]


def test_a_function_around_another_hunks_function_is_shown_from_its_start():
    # `outer` on line 1, `inner` inside it on lines 3 to 14, the rest of `outer` down to line 35.
    text = ('def outer(a):\n    """Outer."""\n    def inner(b):\n' + "".join(f"        inner_{n} = {n}\n" for n in range(1, 11))
            + "        return b\n" + "".join(f"    outer_{n} = {n}\n" for n in range(1, 21)) + "    return inner(a)\n")
    file = adding(text, "inner_9 = 9", "outer_15 = 15")
    assert shown(file) == [list(range(9, 16)), list(range(26, 33))]
    assert shown(with_function(file, text)) == [list(range(1, 26)), list(range(26, 36))]  # lines 1 and 2 are the second hunk's function


def test_a_long_function_keeps_its_start_and_the_lines_nearest_the_hunk():
    file = adding(LONG, "step_100 = 100")
    assert shown(file) == [list(range(99, 106))]
    [hunk] = with_function(file, LONG).hunks
    assert [line.new for line in hunk.lines] == [1, 2, None, *range(60, 145), None]  # 39 lines each side of the hunk
    assert [line.text for line in hunk.lines if line.new is None] == ["(57 unchanged lines not shown)", "(59 unchanged lines not shown)"]
    assert sum(1 for line in hunk.lines if line.new is not None) - 7 == FUNCTION_LINES  # what the hunk brought in
    assert (hunk.old_start, hunk.new_start) == (1, 1) and hunk.lines[-2].old == 143
    rows = render(with_function(file, LONG)).split("\n")
    assert rows[1:5] == ["@@", "     1    def long(a):", '     2        """Add up."""', "          (57 unchanged lines not shown)"]
    assert "(59 unchanged lines not shown)" in render(with_function(file, LONG), style="split").split("__new hunk__")[1]
    [small] = with_function(file, LONG, cap=9).hunks
    assert [line.new for line in small.lines] == [1, 2, None, *range(96, 110), None]


def test_what_one_side_cannot_use_goes_to_the_other():
    [hunk] = with_function(adding(LONG, "step_2 = 2"), LONG).hunks
    assert [line.new for line in hunk.lines] == [*range(1, 88), None]  # nothing above the hunk to show: all 80 lines below it
    assert hunk.lines[-1].text == "(116 unchanged lines not shown)"


def test_a_long_docstring_leaves_room_for_the_code_next_to_the_change():
    # The def, a docstring down to line 61, step_N on line N + 61, the return on line 162.
    text = ('def documented(a):\n    """Summary.\n' + "".join(f"    Detail {n}.\n" for n in range(1, 59)) + '    """\n'
            + "".join(f"    step_{n} = {n}\n" for n in range(1, 101)) + "    return a\n")
    [hunk] = with_function(adding(text, "step_90 = 90"), text).hunks
    assert [line.new for line in hunk.lines] == [*range(1, 27), None, *range(102, 163)]  # a third of the 80 lines for the top
    assert hunk.lines[26].text == "(75 unchanged lines not shown)"


def test_two_hunks_in_one_function_show_each_line_once():
    first, second = with_function(adding(LONG, "step_50 = 50", "step_150 = 150"), LONG).hunks
    numbers = [line.new for hunk in (first, second) for line in hunk.lines if line.new is not None]
    assert numbers == sorted(set(numbers))
    hidden = [int(line.text[1:].split()[0]) for hunk in (first, second) for line in hunk.lines if line.new is None]
    assert hidden == [7, 14, 8] and len(numbers) + sum(hidden) == 203  # every line of the function is shown or counted
    assert second.lines[0].new == 149  # the lines before it went with the first hunk
    assert [line.new for line in first.lines][-41:] == [None, *range(109, 149)]


def test_the_review_shows_the_function_and_places_findings_on_the_diff():
    file = adding(MODULE, "check(path)")
    reply = json.dumps({"summary": "", "findings": [
        {"file": "m.py", "code": "@cache", "line": 6, "severity": "high", "title": "Wrong decorator", "detail": "The cache is wrong.", "fix": "Fix it."},
        {"file": "m.py", "code": "check(path)", "line": 11, "severity": "high", "title": "Bad check", "detail": "The check is wrong.", "fix": "Fix it."},
    ]})
    model = FakeModel(reply)
    policy = apply(parse_policy("review:\n  context_scope: function\n"), "small")
    result = review(title="t", description="", files=[file], policy=policy, model=model, contents={"m.py": MODULE})
    assert "     6    @cache" in model.prompts[0][1]
    assert {f.title: f.line_start for f in result.findings} == {"Wrong decorator": None, "Bad check": 11}  # GitHub can't take line 6

    model = FakeModel(reply)
    review(title="t", description="", files=[file], policy=apply(parse_policy(""), "small"), model=model, contents={"m.py": MODULE})
    assert "@cache" not in model.prompts[0][1]  # without the key, the diff alone
    assert parse_policy("").review.context_scope == "lines"
    assert parse_policy("review:\n  context_scope: block\n").errors == ("review.context_scope must be one of: lines, function.",)


def test_the_per_file_passes_show_the_function_too():
    from stratum_open.review_large import review_files

    policy = apply(parse_policy("review:\n  context_scope: function\n  file_reviews: 1\n  lens_files: 0\n  parallel_calls: 1\n"), "large")
    model = FakeModel(json.dumps({"risk_score": 1, "change_summary": "Adds a check.", "findings": []}))
    review_files(title="t", description="", files=[adding(MODULE, "check(path)")], policy=policy, model=model, contents={"m.py": MODULE})
    assert "     6    @cache" in model.prompts[0][1] and "    11 +          check(path)" in model.prompts[0][1]


def test_a_function_too_big_for_the_call_is_left_out():
    huge = "    x = '" + "y" * 60000 + "'"  # more than a whole chunk of the small profile
    text = "def f():\n" + huge + "\n    a = 1\n    b = 2\n    c = 3\n    d = 4\n    added = 1\n    return 2\n"
    file = adding(text, "added = 1", path="w.py")
    assert "yyyy" in render(with_function(file, text))
    model = FakeModel(json.dumps({"summary": "", "findings": []}))
    review(title="t", description="", files=[file], policy=apply(parse_policy("review:\n  context_scope: function\n"), "small"),
           model=model, contents={"w.py": text})
    assert "added = 1" in model.prompts[0][1] and "yyyy" not in model.prompts[0][1]  # the plain diff


def test_the_runner_fetches_the_text_when_the_review_shows_functions():
    # A .ts file: the checks alone would fetch a Python one, for Bandit.
    text = "".join(f"line {n}\n" for n in range(1, 21))
    for scope, fetched in (("function", True), ("lines", False)):
        gh = FakeGitHub(diff=between(text.replace("line 6\n", ""), text, "x.ts"), files={("x.ts", "a" * 40): text})
        model = FakeModel(json.dumps({"summary": "Adds a line.", "matches_description": "yes", "findings": []}))
        policy = parse_policy(f"review:\n  profile: small\n  context_scope: {scope}\n")
        review_pull(Context(gh=gh, policy=policy, model=model, bot="stratum[bot]", log=lambda line: None), 7)
        assert "     6 +  line 6" in model.prompts[0][1] and ("     1    line 1" in model.prompts[0][1]) is fetched
