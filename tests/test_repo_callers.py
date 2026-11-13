import json

from fakes import FakeModel

from stratum_open.diff import approx_tokens, parse_diff
from stratum_open.policy import parse_policy
from stratum_open.repo.callers import Callers, Site, block, find_callers
from stratum_open.review import review

ON = parse_policy("review:\n  context_callers: 3\n")

HTTP = "import re\n\n\ndef parse_cookie(header):\n    return header.split(';')\n\n\ndef read(path):\n    return open(path).read()\n"
CHECKOUT = {
    "app/__init__.py": "",
    "app/http.py": HTTP,
    "app/request.py": "from app.http import parse_cookie\n\n\ndef cookies(request):\n    raw = request.headers['Cookie']\n    return parse_cookie(raw)\n",
}


def write(root, files):
    for path, text in files.items():
        full = root / path
        full.parent.mkdir(parents=True, exist_ok=True)
        full.write_text(text, encoding="utf-8")
    return str(root)


def change(path, *lines):
    """A diff of `path` from its first line: lines starting "+" are added,
    "-" removed, " " unchanged."""
    old = sum(1 for line in lines if not line.startswith("+"))
    new = len(lines) - sum(1 for line in lines if line.startswith("-"))
    [parsed] = parse_diff("\n".join([f"diff --git a/{path} b/{path}", f"--- a/{path}", f"+++ b/{path}", f"@@ -1,{old} +1,{new} @@", *lines]) + "\n")
    return parsed


# The PR changes what parse_cookie returns.
COOKIE = change("app/http.py", " import re", " ", " ", " def parse_cookie(header):", "-    return header.split(';')", "+    return dict(header.split(';'))")


def found(tmp_path, files, *diffs, policy=ON, **options):
    return find_callers(list(diffs), policy, write(tmp_path, files), **options)


def sites(callers):
    return {(c.name, s.path, s.line) for c in callers for s in c.sites}


# ── what is found ────────────────────────────────────────────────────────


def test_a_call_in_another_file_is_listed_with_the_code_around_it(tmp_path):
    [callers] = found(tmp_path, CHECKOUT, COOKIE)
    assert (callers.path, callers.name, callers.line, callers.more) == ("app/http.py", "parse_cookie", 4, 0)
    [site] = callers.sites
    assert (site.path, site.line, site.within) == ("app/request.py", 6, "cookies")
    assert site.code == ("     4    def cookies(request):\n"
                         "     5        raw = request.headers['Cookie']\n"
                         "     6        return parse_cookie(raw)")


def test_off_by_default_and_nothing_without_a_checkout(tmp_path):
    assert found(tmp_path, CHECKOUT, COOKIE, policy=parse_policy(None)) == []
    assert find_callers([COOKIE], ON, None) == []
    assert parse_policy(None).review.context_callers == 0
    assert parse_policy("review:\n  context_callers: -1\n").errors == ("review.context_callers must be at least 0.",)


def test_an_alias_a_module_attribute_and_a_re_export_are_followed(tmp_path):
    files = dict(CHECKOUT)
    files["app/__init__.py"] = "from .http import parse_cookie\n"
    files["app/alias.py"] = "from app.http import parse_cookie as parse\n\nparse('a=1')\n"
    files["app/module.py"] = "from app import http\n\n\ndef go(h):\n    return http.parse_cookie(h)\n"
    files["app/dotted.py"] = "import app.http\n\napp.http.parse_cookie('x')\n"
    files["app/package.py"] = "from app import parse_cookie\n\nparse_cookie('y')\n"
    assert sites(found(tmp_path, files, COOKIE, policy=parse_policy("review:\n  context_callers: 10\n"))) == {
        ("parse_cookie", "app/request.py", 6), ("parse_cookie", "app/alias.py", 3), ("parse_cookie", "app/module.py", 5),
        ("parse_cookie", "app/dotted.py", 3), ("parse_cookie", "app/package.py", 3),
    }


def test_a_call_in_the_function_s_own_module_counts(tmp_path):
    files = dict(CHECKOUT, **{"app/http.py": HTTP + "\n\ndef header(request):\n    return parse_cookie(request)\n"})
    assert ("parse_cookie", "app/http.py", 13) in sites(found(tmp_path, files, COOKIE))


def test_methods_constructors_and_self_calls(tmp_path):
    shop = ("class Cart:\n"                      # 1
            "    def __init__(self, items):\n"   # 2
            "        self.items = items\n"       # 3
            "\n"
            "    def total(self):\n"             # 5
            "        return sum(self.items)\n"   # 6
            "\n"
            "    def report(self):\n"            # 8
            "        return self.total()\n"      # 9
            "\n"
            "    @staticmethod\n"
            "    def empty():\n"                 # 12
            "        return Cart([])\n")         # 13
    files = {"shop/__init__.py": "", "shop/cart.py": shop,
             "shop/use.py": "from shop.cart import Cart\n\ncart = Cart([1])\nCart.total(cart)\ncart.total()\n"}
    diff = change("shop/cart.py", " class Cart:", " " + shop.split("\n")[1], "-        self.items = items", "+        self.items = list(items)",
                  " ", " " + shop.split("\n")[4], "-        return sum(self.items)", "+        return sum(self.items) + 1")
    assert sites(found(tmp_path, files, diff)) == {
        ("Cart", "shop/cart.py", 13), ("Cart", "shop/use.py", 3),  # the constructor, for a change to __init__
        ("Cart.total", "shop/cart.py", 9), ("Cart.total", "shop/use.py", 4),  # `cart.total()` is on an object: left out
    }


# ── what is never guessed ────────────────────────────────────────────────


def test_a_name_bound_twice_a_star_import_or_another_module_s_name_is_left_out(tmp_path):
    files = dict(CHECKOUT)
    files["app/rebound.py"] = "from app.http import parse_cookie\n\n\ndef go(parse_cookie):\n    return parse_cookie('x')\n"
    files["app/star.py"] = "from app.http import *\n\nparse_cookie('x')\n"
    files["app/other.py"] = "from app.cookies import parse_cookie\n\nparse_cookie('x')\n"
    files["app/cookies.py"] = "def parse_cookie(h):\n    return h\n"
    files["app/word.py"] = "def parse_cookie(h):\n    return h\n\nparse_cookie('x')\n"
    assert sites(found(tmp_path, files, COOKIE)) == {("parse_cookie", "app/request.py", 6)}


def test_calls_the_diff_shows_or_inside_the_function_itself_are_left_out(tmp_path):
    recursive = change("app/http.py", " import re", " ", " ", " def parse_cookie(header):", "-    return header.split(';')",
                       "+    return parse_cookie(header[1:]) if header.startswith(' ') else header.split(';')")
    assert sites(found(tmp_path, CHECKOUT, recursive)) == {("parse_cookie", "app/request.py", 6)}
    request = change("app/request.py", " from app.http import parse_cookie", " ", " ", " def cookies(request):",
                     " " + "    raw = request.headers['Cookie']", "-    return parse_cookie(raw)", "+    return parse_cookie(raw) or {}")
    assert found(tmp_path, CHECKOUT, COOKIE, request) == []  # the only call is on a line the diff shows


def test_at_most_n_calls_each_the_rest_counted(tmp_path):
    files = dict(CHECKOUT)
    files["tests/test_http.py"] = "from app.http import parse_cookie\n\n\ndef test_it():\n    assert parse_cookie('a')\n"
    for name in "bcd":
        files[f"app/{name}.py"] = "from app.http import parse_cookie\n\nparse_cookie('x')\n"
    [callers] = found(tmp_path, files, COOKIE, policy=parse_policy("review:\n  context_callers: 2\n"))
    assert [s.path for s in callers.sites] == ["app/b.py", "app/c.py"]  # the tests come last
    assert callers.more == 3


def test_a_broken_checkout_never_stops_the_review(tmp_path):
    files = dict(CHECKOUT, **{"app/request.py": "def (:\n"})
    assert found(tmp_path, files, COOKIE) == []


# ── the prompt ───────────────────────────────────────────────────────────


ENTRY = Callers("app/http.py", "parse_cookie", 4, [Site("app/request.py", 6, "cookies", "     6        return parse_cookie(raw)")], more=2)


def test_the_block_names_each_call_and_what_was_left_out():
    text = block([ENTRY], None, 1000)
    assert text == ("\nWhere the functions this PR changes are called from, elsewhere in the repository. This code isn't part of the diff: "
                    "use it to check that each change still fits how it is called, and report only lines of the diff.\n"
                    "`parse_cookie` (`app/http.py`, line 4) is called from:\n"
                    "app/request.py:6, in `cookies`:\n     6        return parse_cookie(raw)\n"
                    "(2 more calls not shown.)\n")
    assert block([ENTRY], {"app/other.py"}, 1000) == ""
    assert block([ENTRY], None, 50) == ""  # not even the heading and one call fit


def test_the_block_never_takes_more_than_it_is_given():
    many = Callers("app/http.py", "parse_cookie", 4, [Site("app/request.py", n, "", "x" * 200) for n in range(1, 40)])
    for most in (200, 400, 1000):
        text = block([many], None, most)
        assert text and approx_tokens(text) <= most and text.endswith(" more calls not shown.)\n")


def test_the_review_shows_the_calls_and_takes_their_room_from_the_diff():
    model = FakeModel('{"summary": "", "findings": []}')
    result = review(title="t", description="", files=[COOKIE], policy=ON, model=model, callers=[ENTRY])
    [(_, user)] = model.prompts
    assert "app/request.py:6, in `cookies`:" in user and user.index("app/request.py:6") < user.index("The diff.")
    plain = FakeModel('{"summary": "", "findings": []}')
    review(title="t", description="", files=[COOKIE], policy=ON, model=plain)
    assert "Where the functions" not in plain.prompts[0][1]
    assert result.complete


def test_verify_sees_the_calls_of_the_finding_s_file():
    finding = {"file": "app/http.py", "code": "    return dict(header.split(';'))", "line": 5, "severity": "high", "category": "bug",
               "title": "Callers expect a list", "detail": "cookies() in app/request.py indexes the result.", "fix": "Return a list."}
    model = FakeModel(json.dumps({"summary": "", "findings": [finding]}), '{"real": "yes", "why": "It breaks the caller."}')
    policy = parse_policy("review:\n  context_callers: 3\n  verify: strict\n")
    result = review(title="t", description="", files=[COOKIE], policy=policy, model=model, callers=[ENTRY])
    assert "app/request.py:6" in model.prompts[1][1]
    assert [f.title for f in result.findings] == ["Callers expect a list"]
