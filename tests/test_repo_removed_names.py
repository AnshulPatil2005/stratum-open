import ast

import pytest

from stratum_open.diff import parse_diff
from stratum_open.policy import Policy, parse_policy
from stratum_open.repo import removed_names, unknown_names
from stratum_open.repo.removed_names import removed_name_findings
from stratum_open.repo.unknown_names import unknown_name_findings

ON = parse_policy("checks:\n  removed_names: true\n")
BOTH = parse_policy("checks:\n  removed_names: true\n  unknown_names: true\n")

HTTP = "import re\n\n\ndef parse_cookie(header):\n    return header.split(';')\n\n\ndef read(path):\n    return open(path).read()\n"
CHECKOUT = {
    "app/__init__.py": "",
    "app/http.py": HTTP,
    "app/request.py": "from app.http import parse_cookie, read\n\n\ndef cookies(request):\n    return parse_cookie(request.headers['Cookie'])\n",
}
TITLE = "`app/http.py` no longer has `parse_cookie`, which is still used"


def write(root, files):
    for path, text in files.items():
        full = root / path
        full.parent.mkdir(parents=True, exist_ok=True)
        full.write_text(text, encoding="utf-8")
    return str(root)


def change(path, *lines, new=False, deleted=False):
    """A diff of `path` from its first line: lines starting "+" are added,
    "-" removed, " " unchanged."""
    old = sum(1 for line in lines if not line.startswith("+"))
    header = [f"diff --git a/{path} b/{path}"]
    header += ["new file mode 100644", "--- /dev/null"] if new else ["deleted file mode 100644", f"--- a/{path}"] if deleted else [f"--- a/{path}"]
    header += ["+++ /dev/null" if deleted else f"+++ b/{path}",
               f"@@ -{1 if old else 0},{old} +{0 if deleted else 1},{len(lines) - sum(1 for line in lines if line.startswith('-'))} @@"]
    [parsed] = parse_diff("\n".join(header + list(lines)) + "\n")
    return parsed


def found(tmp_path, files, *diffs, policy=ON, **options):
    return removed_name_findings(list(diffs), policy, write(tmp_path, files), **options)


def http(*lines):
    """A PR changing app/http.py from under its `import re` and two blank lines."""
    return change("app/http.py", " import re", " ", " ", *lines)


RENAME = http("-def parse_cookie(header):", "+def parse_cookies(header):", "     return header.split(';')")
DELETE = http("-def parse_cookie(header):", "-    return header.split(';')", "-", "-", " def read(path):")


# ── what is reported ─────────────────────────────────────────────────────


def test_a_renamed_function_another_file_still_imports_is_a_high_finding(tmp_path):
    [finding] = found(tmp_path, CHECKOUT, RENAME)

    assert (finding.path, finding.line_start, finding.line_end) == ("app/http.py", 4, 4)  # the line with the new name
    assert (finding.severity, finding.category, finding.rule, finding.source) == ("high", "bug", "code/removed-name", "check")
    assert finding.title == TITLE
    assert finding.detail == (
        "This PR removes the function `parse_cookie` from `app/http.py`, and nothing else there binds that name. `app/request.py:1` still "
        "imports it, which raises ImportError as soon as that file is imported. The PR adds `parse_cookies` there, so this looks like a "
        "rename that missed that line."
    )
    assert finding.suggestion == "Change that line to use `parse_cookies`, or keep `parse_cookie` in `app/http.py` as an alias."


def test_a_deleted_function_is_placed_where_it_was(tmp_path):
    [finding] = found(tmp_path, CHECKOUT, DELETE)
    assert (finding.line_start, finding.title) == (4, TITLE)  # `def read(path):`, the first line the diff shows after it
    assert finding.detail.endswith("which raises ImportError as soon as that file is imported.")
    assert finding.suggestion == "Update or remove that line, or keep `parse_cookie` in `app/http.py`."


def test_a_module_attribute_that_is_gone_is_a_medium_finding(tmp_path):
    for bind, use in (
        ("from app import http", "http.parse_cookie(h)"),
        ("import app.http", "app.http.parse_cookie(h)"),
        ("import app.http as web", "web.parse_cookie(h)"),
        ("from . import http", "print(http.parse_cookie)"),
    ):
        files = CHECKOUT | {"app/request.py": f"{bind}\n\n\ndef cookies(h):\n    return {use}\n"}
        [finding] = found(tmp_path, files, RENAME)
        assert (finding.path, finding.line_start, finding.severity) == ("app/http.py", 4, "medium"), bind
        written = use.partition(".parse_cookie")[0].removeprefix("print(")
        assert f"`app/request.py:5` still uses `{written}.parse_cookie`, which raises AttributeError when that line runs." in finding.detail


def test_an_import_continued_with_a_backslash(tmp_path):
    files = CHECKOUT | {"app/request.py": "from app.http import read, \\\n    parse_cookie\n"}
    [finding] = found(tmp_path, files, RENAME)
    assert "`app/request.py:2` still imports it" in finding.detail
    files = CHECKOUT | {"app/request.py": "import re, \\\n    app.http\n\n\ndef cookies(h):\n    return app.http.parse_cookie(h)\n"}
    [finding] = found(tmp_path, files, RENAME)
    assert "`app/request.py:6` still uses `app.http.parse_cookie`" in finding.detail


def test_classes_constants_and_imports_are_names_too(tmp_path):
    files = {
        "shop/__init__.py": "from shop.money import Price\nfrom shop.text import slugify\n",
        "shop/money.py": "class Price:\n    pass\n\n\nRATE = 1.2\nTIMEOUT: int = 3\n",
        "shop/text.py": "def slugify(text):\n    return text\n",
        "shop/cart.py": "from shop import slugify\nfrom shop.money import RATE, TIMEOUT, Price\n",
    }
    money = change("shop/money.py", "-class Price:", "-    pass", "-", "-", "-RATE = 1.2", "-TIMEOUT: int = 3", "+LIMIT = 3")
    init = change("shop/__init__.py", " from shop.money import Price", "-from shop.text import slugify")
    findings = found(tmp_path, files, money, init)
    assert [(f.path, f.line_start, f.title.split("`")[3]) for f in findings] == [
        ("shop/__init__.py", 1, "slugify"), ("shop/money.py", 1, "Price"), ("shop/money.py", 1, "RATE"), ("shop/money.py", 1, "TIMEOUT"),
    ]
    assert findings[0].detail.startswith("This PR removes the import of `slugify` from `shop/__init__.py`, and nothing else there binds that name.")
    assert findings[1].detail.startswith("This PR removes the class `Price` from `shop/money.py`")
    assert findings[2].detail.startswith("This PR removes the assignment of `RATE` from `shop/money.py`")
    # `shop/__init__.py` still imports Price, and fails first.
    assert "`shop/__init__.py:1` still imports it, which raises ImportError as soon as that file is imported. 1 other line still uses it: `shop/cart.py:2`." in findings[1].detail
    assert "rename" not in findings[2].detail  # LIMIT isn't RATE under another name


def test_the_lines_that_still_use_it_are_named_and_counted(tmp_path):
    files = CHECKOUT | {
        "app/views.py": "from app import http\n\n\ndef show(h):\n    return http.parse_cookie(h) or http.parse_cookie('')\n\n\ndef again(h):\n    return http.parse_cookie(h)\n",
        "app/admin.py": "def login(h):\n    from app.http import parse_cookie\n    return parse_cookie(h)\n",
        "tests/test_http.py": "from app.http import parse_cookie\n",
        "tests/test_views.py": "import app.http\n\n\ndef test_it():\n    assert app.http.parse_cookie('a')\n",
    }
    [finding] = found(tmp_path, files, RENAME)
    assert finding.severity == "high"
    # The imports that fail at once come first, then a line of each other file, then the rest.
    assert "`app/request.py:1` still imports it, which raises ImportError as soon as that file is imported. 5 other lines still use it: " \
           "`tests/test_http.py:1`, `app/admin.py:2`, `app/views.py:5` and 2 more." in finding.detail
    assert finding.detail.endswith("so this looks like a rename that missed those lines.")
    assert finding.suggestion == "Change those lines to use `parse_cookies`, or keep `parse_cookie` in `app/http.py` as an alias."


def test_a_name_moved_to_another_file_says_where_it_went(tmp_path):
    files = CHECKOUT | {"app/cookies.py": "def parse_cookie(header):\n    return {}\n", "app/other.py": "import re\n"}
    home = change("app/jar.py", "+def parse_cookie(header):", "+    return header.split(';')", new=True)
    # A file that only imports it, and one that had a definition of that name before the PR, are not where it went.
    imports = change("app/other.py", " import re", "+from app.jar import parse_cookie")
    was_there = change("app/cookies.py", " def parse_cookie(header):", "-    return {}", "+    return None")
    [finding] = found(tmp_path, files, DELETE, home, imports, was_there)
    assert finding.detail.endswith("The PR adds a `parse_cookie` to `app/jar.py`, so this looks like a move that missed that line.")
    assert finding.suggestion == "Import `parse_cookie` from `app/jar.py` in that line, or import it back into `app/http.py`."
    [finding] = found(tmp_path, files, DELETE, imports, was_there)
    assert "a move" not in finding.detail and finding.suggestion == "Update or remove that line, or keep `parse_cookie` in `app/http.py`."


def test_an_import_inside_a_function_fails_only_when_it_runs(tmp_path):
    files = CHECKOUT | {"app/request.py": "def cookies(h):\n    from app.http import parse_cookie\n    return parse_cookie(h)\n"}
    [finding] = found(tmp_path, files, RENAME)
    assert finding.severity == "medium"
    assert "`app/request.py:2` still imports it, which raises ImportError when that function runs." in finding.detail


def test_relative_imports_and_packages_are_followed(tmp_path):
    files = {
        "src/shop/__init__.py": "from .text import slugify\n\nVERSION = '1'\n",
        "src/shop/text.py": "def slugify(text):\n    return text\n",
        "src/shop/core/__init__.py": "",
        "src/shop/core/jobs.py": "from .. import VERSION\nfrom ..text import slugify\n",
        "tests/test_text.py": "from shop import VERSION\nimport shop\n\n\ndef test_it():\n    assert shop.VERSION\n",
    }
    text = change("src/shop/text.py", "-def slugify(text):", "+def slug(text):", "     return text")
    init = change("src/shop/__init__.py", "-from .text import slugify", "+from .text import slug", " ", "-VERSION = '1'")
    findings = found(tmp_path, files, text, init)
    assert [(f.path, f.line_start, f.title.split("`")[3]) for f in findings] == [("src/shop/__init__.py", 2, "VERSION"), ("src/shop/text.py", 1, "slugify")]
    assert "`src/shop/core/jobs.py:1` still imports it" in findings[0].detail and "`tests/test_text.py:1`, `tests/test_text.py:6`" in findings[0].detail
    assert "`src/shop/core/jobs.py:2` still imports it" in findings[1].detail  # the package no longer does: the PR fixed that line


def test_where_the_finding_is_placed(tmp_path):
    # At the end of the file, the last line before the removed ones.
    files = CHECKOUT | {"app/request.py": "from app.http import read\n"}
    at_end = http(" def parse_cookie(header):", "     return header.split(';')", "-", "-", "-def read(path):", "-    return open(path).read()")
    [finding] = found(tmp_path / "end", files, at_end)
    assert (finding.path, finding.line_start, finding.line_end) == ("app/http.py", 5, 5)
    # A hunk that shows no line of the new file: the finding is about the file.
    files = {"app/__init__.py": "", "app/flags.py": "DEBUG = False\n", "app/run.py": "from app.flags import DEBUG\n"}
    [finding] = found(tmp_path / "emptied", files, change("app/flags.py", "-DEBUG = False"))
    assert (finding.path, finding.line_start, finding.line_end) == ("app/flags.py", None, None)
    # Two names that were removed together are two findings on the same line.
    files = CHECKOUT | {"app/request.py": "from app.http import parse_cookie, read\n"}
    both = http("-def parse_cookie(header):", "-    return header.split(';')", "-", "-", "-def read(path):", "-    return open(path).read()", "+def fetch(path):", "+    return path")
    assert [(f.line_start, f.title.split("`")[3]) for f in found(tmp_path / "both", files, both)] == [(4, "parse_cookie"), (4, "read")]


def test_a_use_the_pr_fixes_is_gone_and_one_it_leaves_is_not(tmp_path):
    fixed = change("app/request.py", "-from app.http import parse_cookie, read", "+from app.http import parse_cookies, read")
    assert found(tmp_path, CHECKOUT, RENAME, fixed) == []
    # The PR changes the file, and the old name is still on a line it didn't touch.
    files = CHECKOUT | {"app/request.py": "import os\nfrom app.http import parse_cookie, read\n"}
    touched = change("app/request.py", "-import os", "+import sys", " from app.http import parse_cookie, read")
    [finding] = found(tmp_path, files, RENAME, touched)
    assert "`app/request.py:2` still imports it" in finding.detail


def test_a_file_the_pr_deletes_or_moves_no_longer_uses_it(tmp_path):
    deleted = change("app/request.py", *[f"-{line}" for line in CHECKOUT["app/request.py"].split("\n")[:-1]], deleted=True)
    assert found(tmp_path, CHECKOUT, RENAME, deleted) == []
    [moved] = parse_diff("diff --git a/app/request.py b/app/req.py\nsimilarity index 100%\nrename from app/request.py\nrename to app/req.py\n")
    [finding] = found(tmp_path, CHECKOUT, RENAME, moved)
    assert "`app/req.py:1` still imports it" in finding.detail


def test_a_use_the_pr_adds_is_reported_once(tmp_path):
    added = change("app/views.py", "+from app.http import parse_cookie", new=True)
    files = CHECKOUT | {"app/request.py": ""}
    [finding] = found(tmp_path, files, RENAME, added)
    assert "`app/views.py:1` still imports it" in finding.detail
    # With the unknown-names check on as well, the added line is its finding, on that line.
    assert found(tmp_path, files, RENAME, added, policy=BOTH) == []
    assert [(f.path, f.line_start) for f in unknown_name_findings([RENAME, added], BOTH, str(tmp_path))] == [("app/views.py", 1)]
    # A line the PR doesn't add is still this check's.
    [finding] = found(tmp_path, CHECKOUT, RENAME, added, policy=BOTH)
    assert "`app/request.py:1` still imports it" in finding.detail and "app/views.py" not in finding.detail


def test_a_checkout_that_already_holds_the_change(tmp_path):
    files = CHECKOUT | {"app/http.py": HTTP.replace("parse_cookie", "parse_cookies")}
    assert found(tmp_path, files, RENAME) == []  # read as the default branch, the diff doesn't apply
    [finding] = found(tmp_path, files, RENAME, applied=True)
    assert (finding.line_start, finding.title) == (4, TITLE)
    # The same with the caller's text of the file.
    [finding] = found(tmp_path, files, RENAME, applied=True, contents={"app/http.py": files["app/http.py"]})
    assert finding.title == TITLE


def test_the_callers_own_text_of_a_file_is_used_when_it_agrees_with_the_diff(tmp_path):
    head = HTTP.replace("parse_cookie", "parse_cookies")
    assert len(found(tmp_path, CHECKOUT, RENAME, contents={"app/http.py": head})) == 1
    # The PR's head defines the name further down, where the diff doesn't show it.
    assert found(tmp_path, CHECKOUT, RENAME, contents={"app/http.py": head + "\n\ndef parse_cookie(header):\n    pass\n"}) == []
    # The default branch has since done so: the merge will have it.
    files = CHECKOUT | {"app/http.py": HTTP + "\n\nparse_cookie = read\n"}
    assert found(tmp_path, files, RENAME, contents={"app/http.py": head}) == []


# ── what is left alone ───────────────────────────────────────────────────


def test_off_unless_the_policy_turns_it_on(tmp_path):
    repo = write(tmp_path, CHECKOUT)
    assert not Policy().checks.removed_names
    assert removed_name_findings([RENAME], Policy(), repo) == []
    assert removed_name_findings([RENAME], parse_policy("checks:\n  unknown_names: true\n"), repo) == []
    assert len(removed_name_findings([RENAME], ON, repo)) == 1


def test_without_a_checkout_nothing_is_said(tmp_path):
    assert removed_name_findings([RENAME], ON, None) == []


def test_a_checkout_the_diff_does_not_apply_to_says_nothing(tmp_path):
    # The default branch has changed these lines since the PR branched.
    files = CHECKOUT | {"app/http.py": HTTP.replace("def parse_cookie(header):", "def parse_cookie(header, strict=False):")}
    assert found(tmp_path, files, RENAME) == []


@pytest.mark.parametrize("lines", [
    ["-def parse_cookie(header):", "+def parse_cookies(header):", "     return header.split(';')", "+", "+", "+parse_cookie = parse_cookies"],
    ["-def parse_cookie(header):", "-    return header.split(';')", "+from app.cookies import parse_cookie"],
    ["-def parse_cookie(header):", "-    return header.split(';')", "+from app.cookies import *"],
    ["-def parse_cookie(header):", "+def parse_cookie(header, strict=False):", "     return header.split(';')"],
    ["-def parse_cookie(header):", "-    return header.split(';')", "+if FAST:", "+    from fast import parse_cookie", "+else:", "+    parse_cookie = None"],
    ["-def parse_cookie(header):", "-    return header.split(';')", "+try:", "+    from fast import parse_cookie", "+except ImportError:", "+    pass"],
    ["-def parse_cookie(header):", "+def parse_cookies(header):", "     return header.split(';')", "+", "+", "+def __getattr__(name):",
     "+    if name == 'parse_cookie':", "+        return parse_cookies", "+    raise AttributeError(name)"],
])
def test_a_name_the_module_still_defines_is_not_removed(tmp_path, lines):
    files = CHECKOUT | {"app/cookies.py": "def parse_cookie(header):\n    return header.split(';')\n"}
    assert found(tmp_path, files, http(*lines)) == []


def test_a_name_moved_within_its_file_is_not_removed(tmp_path):
    moved = http("-def parse_cookie(header):", "-    return header.split(';')", "-", "-", " def read(path):", "     return open(path).read()", "+",
                 "+", "+def parse_cookie(header):", "+    return header.split(';')")
    assert found(tmp_path, CHECKOUT, moved) == []


@pytest.mark.parametrize("lines", [
    ["+def __getattr__(name):", "+    return load(name)"],
    ["+from somewhere_else import *"],
    ["+globals().update(load())"],
    ["+import sys", "+sys.modules[__name__] = object()"],
    ["+exec(SOURCE)"],
    ["+def broken(:"],
])
def test_a_module_whose_names_cannot_be_listed_after_the_pr_is_left_alone(tmp_path, lines):
    assert found(tmp_path, CHECKOUT, http("-def parse_cookie(header):", "+def parse_cookies(header):", "     return header.split(';')", *lines)) == []


def test_only_a_line_that_bound_the_name_at_the_top_level_removes_it(tmp_path):
    files = CHECKOUT | {
        "app/http.py": "import re\n\n\nclass Jar:\n    def parse_cookie(self):\n        parse_cookie = 1\n\n\ndef outer():\n    def parse_cookie():\n        pass\n\n\n"
                       "[parse_cookie for parse_cookie in ()]\n",
    }
    diff = http(" class Jar:", "-    def parse_cookie(self):", "-        parse_cookie = 1", "+    pass", " ", " ", " def outer():", "-    def parse_cookie():",
                "-        pass", "+    pass", " ", " ", "-[parse_cookie for parse_cookie in ()]")
    assert found(tmp_path, files, diff) == []


def test_a_dunder_name_is_left_alone(tmp_path):
    files = {"app/__init__.py": "__version__ = '1'\n__all__ = []\n", "setup.py": "from app import __version__, __all__\n"}
    assert found(tmp_path, files, change("app/__init__.py", "-__version__ = '1'", "-__all__ = []", "+VERSION = '1'")) == []


def test_a_module_the_pr_deletes_or_moves_is_left_alone(tmp_path):
    # A module that isn't in the checkout may be written by a build.
    deleted = change("app/http.py", *[f"-{line}" for line in HTTP.split("\n")[:-1]], deleted=True)
    assert found(tmp_path, CHECKOUT, deleted) == []
    [moved] = parse_diff("diff --git a/app/http.py b/app/web.py\nsimilarity index 80%\nrename from app/http.py\nrename to app/web.py\n--- a/app/http.py\n"
                         "+++ b/app/web.py\n@@ -1,5 +1,5 @@\n import re\n \n \n-def parse_cookie(header):\n+def parse_cookies(header):\n     return header.split(';')\n")
    assert found(tmp_path, CHECKOUT, moved) == []
    added = change("app/web.py", "+def parse_cookies(header):", "+    return header", new=True)
    assert found(tmp_path, CHECKOUT, added) == []


@pytest.mark.parametrize("text", [
    "try:\n    from app.http import parse_cookie\nexcept ImportError:\n    parse_cookie = None\n",
    "from app import http\ntry:\n    http.parse_cookie('a')\nexcept AttributeError:\n    pass\n",
    "from contextlib import suppress\nfrom app import http\nwith suppress(AttributeError):\n    http.parse_cookie('a')\n",
    "from typing import TYPE_CHECKING\nif TYPE_CHECKING:\n    from app.http import parse_cookie\n",
    "from app import http\nif hasattr(http, 'parse_cookie'):\n    http.parse_cookie('a')\n",
    "from app import http\nparse = getattr(http, 'parse_cookie', None)\n",
    "import pytest\nfrom app import http\ndef test_gone():\n    with pytest.raises(AttributeError):\n        http.parse_cookie\n",
    "import pytest\ndef test_gone():\n    with pytest.raises(ImportError):\n        from app.http import parse_cookie\n",
    "import pytest\n@pytest.mark.xfail(reason='renamed')\ndef test_later():\n    from app.http import parse_cookie\n",
    "from app import http\ndef show(parse: http.parse_cookie) -> http.parse_cookie:\n    return parse\n",
    # A name in a comment, a string or a list of exports is not a use.
    "# from app.http import parse_cookie\nimport app.http\n__all__ = ['parse_cookie']\nDOC = 'see app.http.parse_cookie'\n",
])
def test_a_use_that_is_guarded_or_is_not_one_is_left_alone(tmp_path, text):
    assert found(tmp_path, CHECKOUT | {"app/request.py": text}, RENAME) == []


@pytest.mark.parametrize("text", [
    "from app.http import *\nparse_cookie('a')\n",
    "from app import http\nhttp = load()\nhttp.parse_cookie('a')\n",
    "from app import http\ndef show(http):\n    return http.parse_cookie('a')\n",
    "try:\n    import fast_http as http\nexcept ImportError:\n    from app import http\nhttp.parse_cookie('a')\n",
    "from other import *\nfrom app import http\nhttp.parse_cookie('a')\n",
    "from app.http import read\nread.parse_cookie\nself.http.parse_cookie('a')\n",
    "from requests import parse_cookie\nimport http\nhttp.parse_cookie('a')\n",
])
def test_a_use_that_is_not_certain_to_be_the_modules_name_is_left_alone(tmp_path, text):
    assert found(tmp_path, CHECKOUT | {"app/request.py": text}, RENAME) == []


def test_a_name_set_from_another_file_is_left_alone(tmp_path):
    for number, patch in enumerate((
        "from app import http\nhttp.parse_cookie = str\n",
        "import app.http\nsetattr(app.http, 'parse_cookie', str)\n",
        "def test(monkeypatch):\n    monkeypatch.setattr(http, 'parse_cookie', str, raising=False)\n",
        "from app import http\nfor name in NAMES:\n    setattr(http, name, str)\n",
        "import app\napp.http.__dict__.update(EXTRA)\n",
    )):
        assert found(tmp_path / str(number), CHECKOUT | {"tests/conftest.py": patch}, RENAME) == [], patch
    assert len(found(tmp_path / "all", CHECKOUT | {"app/api.py": "__all__ = ['parse_cookie']\n"}, RENAME)) == 1


def test_an_import_that_could_be_two_files_is_left_alone(tmp_path):
    files = {"src/app/__init__.py": "", "src/app/http.py": HTTP, "tests/test_http.py": "from app.http import parse_cookie\n"}
    rename = change("src/app/http.py", " import re", " ", " ", "-def parse_cookie(header):", "+def parse_cookies(header):")
    assert len(found(tmp_path / "one", files, rename)) == 1
    # The same package in two places, a package with no __init__.py, a compiled twin, and a module that is also a folder.
    assert found(tmp_path / "two", files | {"app/__init__.py": "", "app/http.py": ""}, rename) == []
    assert found(tmp_path / "namespace", {path: text for path, text in files.items() if "__init__" not in path}, rename) == []
    assert found(tmp_path / "compiled", files | {"src/app/http.pyx": ""}, rename) == []
    assert found(tmp_path / "folder", files | {"src/app/http/__init__.py": ""}, rename) == []


def test_a_use_in_a_file_the_pr_changes_out_of_sight_is_left_alone(tmp_path):
    # The diff of the file that used it doesn't apply to the checkout, so what it holds after the PR isn't known.
    unseen = change("app/request.py", "-from app.http import parse_cookies, read", "+from app.http import read, parse_cookies")
    assert found(tmp_path, CHECKOUT, RENAME, unseen) == []
    [binary] = parse_diff("diff --git a/app/request.py b/app/request.py\nBinary files a/app/request.py and b/app/request.py differ\n")
    assert found(tmp_path, CHECKOUT, RENAME, binary) == []


def test_files_that_are_not_the_projects_python_are_left_alone(tmp_path):
    lines = (" import re", " ", " ", "-def parse_cookie(header):", "+def parse_cookies(header):")
    for path in ("app/.hidden/http.py", "vendor/http.py", "app/http.txt"):
        files = {"app/__init__.py": "", path: HTTP, "run.py": "from app.http import parse_cookie\nimport http\nhttp.parse_cookie\n"}
        assert found(tmp_path / path.replace("/", "-"), files, change(path, *lines)) == [], path
    # A use in a file that doesn't parse, or in a folder that isn't read.
    for path, text in (("app/request.py", "from app.http import parse_cookie\ndef broken(:\n"), ("node_modules/x/run.py", "from app.http import parse_cookie\n")):
        files = CHECKOUT | {"app/request.py": ""} | {path: text}
        assert found(tmp_path / path.replace("/", "-"), files, RENAME) == [], path


def test_the_caps(tmp_path, monkeypatch):
    repo = write(tmp_path, CHECKOUT | {"app/views.py": "from app.http import parse_cookie\n"})
    assert len(removed_name_findings([RENAME], ON, repo)) == 1
    for module, name, value in ((unknown_names, "MAX_FILES", 2), (unknown_names, "MAX_PARSED", 0), (removed_names, "MAX_PR_FILES", 1),
                                (removed_names, "MAX_CHECKED", 0), (removed_names, "MAX_NAMES", 0)):
        with monkeypatch.context() as patch:
            patch.setattr(module, name, value)
            assert removed_name_findings([RENAME], ON, repo) == [], name
    # More files mention the name than are read: what was read is still reported.
    monkeypatch.setattr(removed_names, "MAX_READ", 1)
    [finding] = removed_name_findings([RENAME], ON, repo)
    assert "`app/request.py:1` still imports it" in finding.detail and "app/views.py" not in finding.detail


def test_how_each_removed_name_came_out(tmp_path):
    files = CHECKOUT | {
        "app/http.py": HTTP + "\n\nLIMIT = 1\nRETRIES = 2\nDEBUG = False\n\n\ndef old():\n    pass\n",
        # RETRIES is imported, from somewhere that isn't read. DEBUG and old are words here, where no use could be.
        "app/views.py": "from app import http\nfrom settings import RETRIES\nprint(http.LIMIT)\n\n\ndef old(DEBUG=1):  # not http.DEBUG\n    return [DEBUG]\n",
        "tests/conftest.py": "from app import http\nhttp.LIMIT = 5\n",
    }
    diff = http("-def parse_cookie(header):", "+def parse_cookies(header):", "     return header.split(';')", " ", " ", "-def read(path):", "+def read(path, mode='r'):",
                "     return open(path).read()", " ", " ", "-LIMIT = 1", "-RETRIES = 2", "-DEBUG = False", " ", " ", "-def old():", "+def new():")
    removed, tally = removed_names._decide([diff], write(tmp_path, files), {}, False)
    assert {item.name: item.outcome for item in removed} == {
        "parse_cookie": removed_names.REPORTED, "LIMIT": removed_names.SET_ELSEWHERE, "RETRIES": removed_names.UNPROVEN,
        "DEBUG": removed_names.UNUSED, "old": removed_names.UNUSED,
    }
    assert tally == {
        "module: read": 1, "name: its line is removed, and the module still defines it: nothing to report": 1,
        "name: " + removed_names.REPORTED: 1, "name: " + removed_names.SET_ELSEWHERE: 1, "name: " + removed_names.UNPROVEN: 1,
        "name: " + removed_names.UNUSED: 2,
    }


def test_where_a_file_could_be_using_a_name():
    text = ("from app.http import (\n    read,\n    parse as p,  # noqa\n    first, second)\nimport app.http as web, other\nx = web.find(1)\n"
            "y = web.\\\n    chained\n# web.commented, and a word\nwords = [plain]  # with trailing\nz = call(argument)\nfrom a import b; lone\n")
    could = {name: removed_names._could_use(text, text.index(name)) for name in (
        "read", "parse", "first", "second", "other", "find", "chained", "commented", "word", "plain", "trailing", "argument", "lone")}
    assert [name for name, yes in could.items() if yes] == ["read", "parse", "first", "second", "other", "find", "chained", "lone"]


def test_what_counts_as_bound_before_the_pr():
    tree = ast.parse("import os.path, json as j\nfrom a import b as c, d\nfrom e import *\nX: int = 1\nY = Z = 2\nfor i in ():\n    pass\n"
                     "if X:\n    def f(): pass\nelse:\n    class f: pass\n[n for n in ()]\ng = lambda arg: arg\ndef h():\n    global late\n    inner = 1\n")
    assert removed_names._defined(tree) == {
        "os": [(1, "import")], "j": [(1, "import")], "c": [(2, "import")], "d": [(2, "import")], "X": [(4, "name")], "Y": [(5, "name")],
        "Z": [(5, "name")], "i": [(6, "name")], "f": [(11, "class"), (9, "function")], "g": [(13, "name")], "h": [(14, "function")],
    }


def test_an_error_becomes_a_low_finding_and_never_raises(tmp_path, monkeypatch):
    def broken(*args, **kwargs):
        raise RuntimeError("no such luck")

    monkeypatch.setattr(removed_names, "_decide", broken)
    [finding] = found(tmp_path, CHECKOUT, RENAME)
    assert (finding.severity, finding.rule, finding.source) == ("low", "checks/error", "check")
    assert finding.title == "The removed names check didn't run"
    assert "RuntimeError: no such luck" in finding.detail
