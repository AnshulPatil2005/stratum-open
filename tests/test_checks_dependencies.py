from dataclasses import replace
from datetime import datetime, timedelta, timezone

import pytest
import requests

from stratum_open.checks import dependencies
from stratum_open.checks.dependencies import PackageInfo, PackageLookup, find_new, lookalike, normalise
from stratum_open.diff import parse_diff
from stratum_open.policy import Dependencies, Policy

NOW = datetime(2026, 11, 2, tzinfo=timezone.utc)


def diff(path, added=(), removed=(), context=(), *, start=1, new=False):
    """A one-hunk git diff: the context lines, then the removed, then the added."""
    head = [f"diff --git a/{path} b/{path}"]
    head += ["new file mode 100644", "--- /dev/null"] if new else [f"--- a/{path}"]
    head.append(f"+++ b/{path}")
    head.append(f"@@ -{start},{len(context) + len(removed)} +{start},{len(context) + len(added)} @@")
    body = [f" {line}" for line in context] + [f"-{line}" for line in removed] + [f"+{line}" for line in added]
    return "\n".join(head + body) + "\n"


def new_names(text, contents=None):
    return [(d.ecosystem, d.name, d.line, d.from_registry) for d in find_new(parse_diff(text), contents)]


def check(text, policy=None, lookup=None, contents=None):
    return dependencies.check(parse_diff(text), policy or Policy(), lookup=lookup, contents=contents, now=NOW)


def rules(findings):
    return [f.rule for f in findings]


# ── requirements files ───────────────────────────────────────────────────


def test_a_new_requirement_is_found():
    assert new_names(diff("requirements.txt", ["httpx>=0.27"], context=["flask==3.0"])) == [("pypi", "httpx", 2, True)]


def test_a_version_bump_is_not_new():
    assert new_names(diff("requirements.txt", ["Flask==3.1"], removed=["flask==3.0"])) == []


def test_options_comments_urls_and_paths_are_not_dependencies():
    lines = [
        "# tools", "-r base.txt", "-e .", "--index-url https://pypi.example/simple", "./vendor/pkg",
        "https://files.example/pkg-1.0.tar.gz", "git+https://github.com/x/y.git#egg=y", "",
        "    --hash=sha256:abcd", "rich[jupyter]>=13 ; python_version >= '3.11'  # pretty output",
    ]
    assert new_names(diff("requirements-dev.txt", lines)) == [("pypi", "rich", 10, True)]


def test_a_requirement_from_a_url_is_new_but_not_from_the_registry():
    assert new_names(diff("requirements.txt", ["mylib @ git+https://github.com/me/mylib.git"])) == [("pypi", "mylib", 1, False)]


@pytest.mark.parametrize("path,kind", [
    ("requirements.txt", "requirements"), ("requirements/dev.in", "requirements"), ("dev-requirements.txt", "requirements"),
    ("requirements_test.txt", "requirements"), ("MANIFEST.in", None), ("docs/notes.txt", None),
    ("web/package.json", "npm"), ("package-lock.json", None), ("pyproject.toml", "pyproject"), ("crates/a/Cargo.toml", "cargo"),
    ("go.mod", "go"), ("go.sum", None),
])
def test_manifests_are_recognised(path, kind):
    assert dependencies.manifest_kind(path) == kind


def test_moving_a_dependency_between_files_is_not_new():
    text = diff("requirements.txt", [], removed=["httpx>=0.27"]) + diff(
        "pyproject.toml", ['    "httpx>=0.27",'], context=["[project]", "dependencies = ["])
    assert new_names(text) == []


def test_pypi_names_are_normalised():
    assert normalise("Typing_Extensions", "pypi") == "typing-extensions"
    assert normalise("zope.interface", "pypi") == "zope-interface"
    assert normalise("serde_json", "crates") == "serde-json"
    assert new_names(diff("requirements.txt", ["typing_extensions>=4"], removed=["Typing-Extensions>=3"])) == []


# ── pyproject.toml ───────────────────────────────────────────────────────

PYPROJECT = """[build-system]
requires = ["setuptools>=68"]

[project]
name = "demo"
keywords = ["review", "pytest"]
classifiers = [
    "License :: OSI Approved :: MIT License",
]
dependencies = [
    "requests>=2.31",
    "click",
]

[project.optional-dependencies]
test = ["pytest>=8", "hypothesis"]

[dependency-groups]
dev = [{include-group = "test"}, "ruff"]

[tool.poetry.dependencies]
python = "^3.11"
rich = "^13"
mylocal = { path = "../mylocal" }
mygit = { git = "https://github.com/me/mygit.git" }

[tool.ruff]
select = ["E", "F"]
"""


def test_pyproject_dependencies_are_found_in_every_section():
    lines = PYPROJECT.splitlines()
    found = new_names(diff("pyproject.toml", lines, new=True))
    assert [(name, registry) for _, name, _, registry in found] == [
        ("setuptools", True), ("requests", True), ("click", True), ("pytest", True), ("hypothesis", True),
        ("ruff", True), ("rich", True), ("mygit", False),
    ]
    assert found[2][2] == lines.index('    "click",') + 1


def test_a_pyproject_line_whose_section_is_hidden_must_look_like_a_requirement():
    text = (
        "diff --git a/pyproject.toml b/pyproject.toml\n--- a/pyproject.toml\n+++ b/pyproject.toml\n"
        "@@ -20,3 +20,6 @@\n"
        '     "requests>=2.31",\n'
        '+    "httpx>=0.27",\n'
        '+    "anyio",\n'
        '+    "Programming Language :: Python :: 3",\n'
        "     ]\n"
    )
    assert new_names(text) == [("pypi", "httpx", 21, True)]
    # With the whole file the section is known, and a bare name counts too.
    full = "\n".join(["# pad"] * 17 + ["[project]", "dependencies = [", '    "requests>=2.31",', '    "httpx>=0.27",', '    "anyio",',
                                      '    "Programming Language :: Python :: 3",', "]"])
    assert [n for _, n, _, _ in new_names(text, {"pyproject.toml": full})] == ["httpx", "anyio"]


def test_a_visible_dependencies_array_counts_bare_names_without_its_table():
    text = diff("pyproject.toml", ['    "anyio",'], context=["dependencies = [", '    "httpx>=0.27",'], start=30)
    assert new_names(text) == [("pypi", "anyio", 32, True)]


def test_a_dependency_already_listed_elsewhere_in_the_file_is_not_new():
    text = diff("pyproject.toml", ['test = ["pytest>=8", "requests"]'],
                context=["[project]", "dependencies = [", '    "requests>=2",', "]", "[project.optional-dependencies]"])
    assert [n for _, n, _, _ in new_names(text)] == ["pytest"]


# ── package.json ─────────────────────────────────────────────────────────

PACKAGE_JSON = """{
  "name": "demo",
  "version": "1.0.0",
  "scripts": {
    "test": "jest",
    "lint": "eslint ."
  },
  "engines": {
    "node": ">=18"
  },
  "dependencies": {
    "express": "^4.19.2",
    "lodash4": "npm:lodash@^4.17.21",
    "@acme/utils": "workspace:*",
    "my-fork": "github:me/my-fork#main"
  },
  "devDependencies": {
    "@types/node": "^20.0.0",
    "jest": "29.x"
  }
}"""


def test_package_json_dependencies_are_found_in_their_sections():
    found = new_names(diff("package.json", PACKAGE_JSON.splitlines(), new=True))
    assert [(name, registry) for _, name, _, registry in found] == [
        ("express", True), ("lodash", True), ("my-fork", False), ("@types/node", True), ("jest", True),
    ]


def test_a_package_json_line_whose_section_is_hidden_is_judged_by_its_value():
    text = (
        "diff --git a/package.json b/package.json\n--- a/package.json\n+++ b/package.json\n"
        "@@ -30,2 +30,7 @@\n"
        '     "react": "^18.3.0",\n'
        '+    "react-dom": "^18.3.0",\n'
        '+    "version": "2.0.0",\n'
        '+    "node": ">=20",\n'
        '+    "build": "tsc -p .",\n'
        '+    "@acme/ui": "workspace:^",\n'
        "   },\n"
    )
    assert new_names(text) == [("npm", "react-dom", 31, True)]


def test_a_package_json_version_bump_is_not_new():
    assert new_names(diff("package.json", ['    "express": "^4.20.0",'], removed=['    "express": "^4.19.2",'])) == []


def test_moving_a_package_to_dev_dependencies_is_not_new():
    text = diff("package.json", ['    "jest": "^29.0.0"'], removed=['    "jest": "^29.0.0",'], context=['  "devDependencies": {'])
    assert new_names(text) == []


# ── Cargo.toml and go.mod ────────────────────────────────────────────────

CARGO = """[package]
name = "demo"
version = "0.1.0"
edition = "2021"

[dependencies]
serde = { version = "1", features = ["derive"] }
tokio = "1.38"
json = { package = "serde_json", version = "1" }
local = { path = "../local" }
shared = { workspace = true }
fromgit = { git = "https://github.com/me/fromgit" }

[target.'cfg(windows)'.dependencies]
winreg = "0.52"

[dependencies.regex]
version = "1.10"

[features]
default = ["std"]
"""


def test_cargo_dependencies_are_found():
    found = new_names(diff("Cargo.toml", CARGO.splitlines(), new=True))
    assert [(name, registry) for _, name, _, registry in found] == [
        ("serde", True), ("tokio", True), ("serde_json", True), ("fromgit", False), ("winreg", True), ("regex", True),
    ]


def test_a_cargo_line_whose_section_is_hidden_must_look_like_a_dependency():
    text = (
        "diff --git a/Cargo.toml b/Cargo.toml\n--- a/Cargo.toml\n+++ b/Cargo.toml\n"
        "@@ -12,1 +12,4 @@\n"
        ' anyhow = "1"\n'
        '+thiserror = "1.0"\n'
        '+edition = "2021"\n'
        '+lto = "fat"\n'
    )
    assert new_names(text) == [("crates", "thiserror", 13, True)]


GO_MOD = """module example.com/demo

go 1.22

require github.com/spf13/cobra v1.8.0

require (
	golang.org/x/sync v0.7.0
	github.com/inconshreveable/mousetrap v1.1.0 // indirect
)

replace github.com/old/thing => github.com/new/thing v1.0.0
"""


def test_go_requirements_are_found_and_indirect_ones_left_out():
    found = new_names(diff("go.mod", GO_MOD.splitlines(), new=True))
    assert [(name, line) for _, name, line, _ in found] == [("github.com/spf13/cobra", 5), ("golang.org/x/sync", 8)]


def test_go_lines_whose_block_is_hidden():
    text = diff("go.mod", ["require github.com/spf13/pflag v1.0.5", "\tgithub.com/acme/thing v0.3.0", "\tgithub.com/a/b v1.0.0 // indirect"],
                context=["\tgolang.org/x/sync v0.7.0"], start=12)
    assert [name for _, name, _, _ in new_names(text)] == ["github.com/spf13/pflag", "github.com/acme/thing"]


def test_a_go_version_bump_is_not_new():
    assert new_names(diff("go.mod", ["\tgolang.org/x/sync v0.8.0"], removed=["\tgolang.org/x/sync v0.7.0"], context=["require ("])) == []


# ── findings for new dependencies ────────────────────────────────────────


def test_each_new_dependency_is_a_low_supply_chain_finding():
    (finding,) = check(diff("requirements.txt", ["flask==3.0"], context=["click==8"]))
    assert finding.rule == "deps/new" and finding.severity == "low" and finding.category == "supply-chain"
    assert finding.needs_human is True and finding.line_start == 2 and "`flask`" in finding.detail


# ── look-alike names ─────────────────────────────────────────────────────


@pytest.mark.parametrize("name,ecosystem,similar", [
    ("reqeusts", "pypi", "requests"),
    ("requets", "pypi", "requests"),
    ("tqmd", "pypi", "tqdm"),
    ("python_dateutil2", "pypi", "python-dateutil"),
    ("pythondateutil", "pypi", "python-dateutil"),
    ("requests-oauthlb", "pypi", "requests-oauthlib"),
    ("scikit-lean", "pypi", "scikit-learn"),
    ("djangorestframwork", "pypi", "djangorestframework"),
    ("reactdom", "npm", "react-dom"),
    ("expresss", "npm", "express"),
    ("@bable/core", "npm", "@babel/core"),
    ("tokioo", "crates", "tokio"),
    ("serde-json", "crates", None),  # the same crate: crates.io treats - and _ alike
    ("pytest-env", "pypi", None),  # a different plugin, not a typo of pytest-cov
    ("preact", "npm", None),
    ("sha3", "crates", None),
    ("numba", "pypi", None),
    ("requests", "pypi", None),
    ("cobra", "go", None),
])
def test_lookalike_names(name, ecosystem, similar):
    assert lookalike(name, ecosystem) == similar


def test_a_lookalike_is_high_and_needs_a_maintainer():
    findings = check(diff("requirements.txt", ["reqeusts==2.31"]))
    finding = next(f for f in findings if f.rule == "deps/lookalike")
    assert finding.severity == "high" and finding.needs_human and "`reqeusts` looks like `requests`" == finding.title


def test_a_dependency_from_git_gets_no_lookalike_check():
    assert rules(check(diff("requirements.txt", ["reqeusts @ git+https://example.org/r.git"]))) == ["deps/new"]


# ── registry lookups ─────────────────────────────────────────────────────


class FakeLookup:
    def __init__(self, answers):
        self.answers = answers
        self.asked = []

    def info(self, ecosystem, name):
        self.asked.append((ecosystem, name))
        answer = self.answers.get(name)
        if isinstance(answer, Exception):
            raise answer
        return answer


def test_a_package_missing_from_its_registry_is_high():
    lookup = FakeLookup({"fastjsonx": PackageInfo(exists=False)})
    findings = check(diff("requirements.txt", ["fastjsonx==1.0"]), lookup=lookup)
    finding = next(f for f in findings if f.rule == "deps/missing")
    assert finding.severity == "high" and finding.needs_human and "slopsquatting" in finding.detail
    assert "PyPI" in finding.title and lookup.asked == [("pypi", "fastjsonx")]


def test_a_brand_new_package_is_high_and_says_its_age():
    lookup = FakeLookup({"left-padx": PackageInfo(True, NOW - timedelta(days=3))})
    findings = check(diff("package.json", ['    "left-padx": "^1.0.0"'], context=["{", '  "dependencies": {']), lookup=lookup)
    finding = next(f for f in findings if f.rule == "deps/young")
    assert finding.severity == "high" and finding.needs_human and "3 days ago" in finding.detail
    assert lookup.asked == [("npm", "left-padx")]


def test_an_established_package_gets_no_registry_finding():
    lookup = FakeLookup({"flask": PackageInfo(True, NOW - timedelta(days=5000))})
    assert rules(check(diff("requirements.txt", ["flask==3.0"]), lookup=lookup)) == ["deps/new"]


def test_the_age_threshold_comes_from_the_policy():
    lookup = FakeLookup({"newish": PackageInfo(True, NOW - timedelta(days=45))})
    strict = replace(Policy(), dependencies=Dependencies(min_package_age_days=60))
    assert "deps/young" not in rules(check(diff("requirements.txt", ["newish==1"]), lookup=lookup))
    assert "deps/young" in rules(check(diff("requirements.txt", ["newish==1"]), strict, lookup=lookup))


@pytest.mark.parametrize("answer", [None, requests.ConnectionError("offline"), RuntimeError("bug in a lookup")])
def test_a_lookup_that_cant_answer_gives_no_finding(answer):
    lookup = FakeLookup({"flask": answer})
    assert rules(check(diff("requirements.txt", ["flask==3.0"]), lookup=lookup)) == ["deps/new"]


def test_go_modules_and_git_dependencies_are_not_looked_up():
    lookup = FakeLookup({})
    text = diff("go.mod", ["require github.com/spf13/cobra v1.8.0"]) + diff("requirements.txt", ["x @ git+https://e.org/x.git"])
    check(text, lookup=lookup)
    assert lookup.asked == []


def test_lookups_stop_at_the_cap(monkeypatch):
    monkeypatch.setattr(dependencies, "MAX_LOOKUPS", 2)
    lookup = FakeLookup({})
    check(diff("requirements.txt", ["aaa-one==1", "bbb-two==1", "ccc-three==1"]), lookup=lookup)
    assert [name for _, name in lookup.asked] == ["aaa-one", "bbb-two"]


# ── PackageLookup against fake registries ────────────────────────────────


class FakeResponse:
    def __init__(self, status, data=None):
        self.status_code = status
        self._data = data

    def json(self):
        if isinstance(self._data, Exception):
            raise self._data
        return self._data


class FakeSession:
    def __init__(self, replies):
        self.replies = replies
        self.calls = []

    def get(self, url, timeout=None, headers=None):
        self.calls.append((url, timeout, headers))
        reply = self.replies[url]
        if isinstance(reply, Exception):
            raise reply
        return reply


def test_pypi_reports_the_earliest_upload():
    url = "https://pypi.org/pypi/my-pkg/json"
    releases = {
        "1.0": [{"upload_time_iso_8601": "2024-03-01T10:00:00.000000Z"}],
        "0.1": [{"upload_time": "2023-01-15T08:30:00"}, {"upload_time_iso_8601": "2023-01-15T08:31:00Z"}],
        "0.0.1": [],
    }
    session = FakeSession({url: FakeResponse(200, {"releases": releases})})
    info = PackageLookup(session).info("pypi", "My_Pkg")
    assert info == PackageInfo(True, datetime(2023, 1, 15, 8, 30, tzinfo=timezone.utc))
    assert session.calls[0][1] == 10.0 and "stratum-open" in session.calls[0][2]["User-Agent"]


def test_npm_scoped_names_are_encoded_and_created_is_read():
    url = "https://registry.npmjs.org/@acme%2Fwidget"
    session = FakeSession({url: FakeResponse(200, {"time": {"created": "2026-10-30T12:00:00.000Z"}})})
    info = PackageLookup(session).info("npm", "@acme/widget")
    assert info.exists and info.first_published == datetime(2026, 10, 30, 12, tzinfo=timezone.utc)


def test_crates_io_gets_a_user_agent_and_created_at_is_read():
    url = "https://crates.io/api/v1/crates/serde"
    session = FakeSession({url: FakeResponse(200, {"crate": {"created_at": "2014-12-05T20:20:39.487502+00:00"}})})
    info = PackageLookup(session).info("crates", "serde")
    assert info.first_published.year == 2014
    assert session.calls[0][2]["User-Agent"].startswith("stratum-open/")


def test_a_404_means_missing():
    session = FakeSession({"https://pypi.org/pypi/nope-nope/json": FakeResponse(404)})
    assert PackageLookup(session).info("pypi", "nope-nope") == PackageInfo(exists=False)


@pytest.mark.parametrize("reply", [
    FakeResponse(500), FakeResponse(429), FakeResponse(200, ValueError("not json")), FakeResponse(200, ["odd"]),
    requests.Timeout("slow"), requests.ConnectionError("offline"),
])
def test_anything_else_is_unknown(reply):
    session = FakeSession({"https://pypi.org/pypi/flask/json": reply})
    assert PackageLookup(session).info("pypi", "flask") is None


def test_answers_are_cached_for_the_run():
    session = FakeSession({"https://pypi.org/pypi/flask/json": FakeResponse(200, {"releases": {}})})
    lookup = PackageLookup(session)
    assert lookup.info("pypi", "flask") == lookup.info("pypi", "Flask") == PackageInfo(True, None)
    assert len(session.calls) == 1


def test_go_modules_have_no_lookup():
    session = FakeSession({})
    assert PackageLookup(session).info("go", "github.com/spf13/cobra") is None and session.calls == []
