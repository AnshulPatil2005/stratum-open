import os
import subprocess
from datetime import datetime, timedelta, timezone
from pathlib import Path

import pytest

from stratum_open.repo.hotspots import Hotspot, decision_points, hotspot_note, hotspot_paths, hotspots, is_source
from stratum_open.repo.ownership import ModuleOwners, bus_factor_notes, module_of, owners

NOW = datetime(2026, 11, 1, tzinfo=timezone.utc)
START = NOW - timedelta(days=100)
MAINTAINER = "maint@example.com"
ALICE = "123+Alice@users.noreply.github.com"
OLD_ALICE = "alice@old.example"
BOB = "bob@example.com"
BOT = "49699333+dependabot[bot]@users.noreply.github.com"


class Repo:
    def __init__(self, root: Path):
        self.root = root / "repo"
        self.root.mkdir()
        self.path = str(self.root)
        empty = root / "empty-gitconfig"
        empty.write_text("")
        self.env = {**os.environ, "GIT_CONFIG_GLOBAL": str(empty), "GIT_CONFIG_NOSYSTEM": "1"}
        self.git("init", "-q", "-b", "main")

    def git(self, *args, email=MAINTAINER, day=0):
        stamp = (START + timedelta(days=day)).strftime("%Y-%m-%dT%H:%M:%S+0000")
        env = {
            **self.env,
            "GIT_AUTHOR_NAME": email.split("@")[0], "GIT_AUTHOR_EMAIL": email, "GIT_AUTHOR_DATE": stamp,
            "GIT_COMMITTER_NAME": "maint", "GIT_COMMITTER_EMAIL": MAINTAINER, "GIT_COMMITTER_DATE": stamp,
        }
        return subprocess.run(["git", "-C", self.path, *args], env=env, check=True, capture_output=True, text=True).stdout.strip()

    def commit(self, files, *, email=MAINTAINER, day, message="change"):
        """Writes `files` ({path: text}) and commits them."""
        for path, text in files.items():
            full = self.root / path
            full.parent.mkdir(parents=True, exist_ok=True)
            full.write_text(text, encoding="utf-8")
        self.git("add", "-A", email=email, day=day)
        self.git("commit", "-q", "-m", message, email=email, day=day)

    def clone(self, target: Path, depth: int) -> str:
        self.git("clone", "-q", "--depth", str(depth), self.root.as_uri(), str(target))
        return str(target)


@pytest.fixture
def repo(tmp_path):
    return Repo(tmp_path)


def branches(n):
    return "def f(x):\n" + "    if x:\n        pass\n" * n


# ── ownership ────────────────────────────────────────────────────────────


def test_module_of():
    assert module_of("backend/api/x.py") == "backend/api"
    assert module_of("backend/api/v1/x.py") == "backend/api"
    assert module_of("backend/x.py") == "backend"
    assert module_of("setup.py") == "."


def test_owners_counts_commits_per_author_per_module(repo):
    repo.commit({".mailmap": f"Alice <{ALICE}> <{OLD_ALICE}>\n", "README.md": "hi"}, day=0)  # first commit: not counted
    repo.commit({"backend/billing/a.py": "1", "backend/billing/b.py": "1"}, email=ALICE, day=1)  # once, for two files
    repo.commit({"backend/billing/a.py": "2"}, email=OLD_ALICE, day=2)  # .mailmap makes this alice
    repo.commit({"backend/billing/a.py": "3"}, email=BOT, day=3)  # bots aren't counted
    repo.commit({"backend/api/x.py": "1"}, email=BOB, day=4)
    repo.commit({"setup.py": "1"}, email=BOB, day=5)
    repo.git("checkout", "-q", "-b", "feature")
    repo.commit({"backend/api/y.py": "1"}, email=BOB, day=6)
    repo.git("checkout", "-q", "main")
    repo.git("merge", "-q", "--no-ff", "feature", "-m", "Merge feature", email=ALICE, day=7)  # merges aren't counted
    (repo.root / "web").mkdir()
    repo.git("mv", "backend/api/x.py", "web/x.py", day=8)
    repo.git("commit", "-q", "-m", "move", email=BOB, day=8)  # counts for both ends

    result = owners(repo.path, now=NOW)
    assert {module: entry.authors for module, entry in result.items()} == {
        "backend/billing": {"alice": 2},
        "backend/api": {"bob@example.com": 3},
        "web": {"bob@example.com": 1},
        ".": {"bob@example.com": 1},
    }
    assert result["backend/api"].commits == 3
    assert set(owners(repo.path, now=NOW, exclude=["bob@example.com"])) == {"backend/billing"}
    assert {module: entry.authors for module, entry in owners(repo.path, now=NOW, window_days=93).items()} == {
        "backend/api": {"bob@example.com": 1},  # only the move, on day 8
        "web": {"bob@example.com": 1},
    }


def billing(*counts):
    authors = dict(counts)
    return {"backend/billing": ModuleOwners("backend/billing", 180, authors)}


def test_bus_factor_names_the_only_recent_author():
    notes = bus_factor_notes(billing(("alice", 14), ("bob@example.com", 1)), ["backend/billing/charge.py", "backend/billing/refund.py"], "carol")
    assert notes == [
        "Only `alice` has changed `backend/billing` in the last 180 days (14 of 15 commits); a maintainer may want them to review this."
    ]


@pytest.mark.parametrize(
    "counts, noted",
    [
        ((("alice", 4), ("bob", 1)), True),  # exactly 80% of 5
        ((("alice", 4),), False),  # 4 commits say too little
        ((("alice", 5), ("bob", 2)), False),  # 71%
        ((("alice", 8), ("bob", 1), ("dan", 1)), True),  # 80% of 10
    ],
)
def test_bus_factor_threshold(counts, noted):
    assert bool(bus_factor_notes(billing(*counts), ["backend/billing/x.py"], "carol")) is noted


def test_no_bus_factor_note_when_the_pr_author_is_the_only_author():
    assert bus_factor_notes(billing(("alice", 9)), ["backend/billing/x.py"], "Alice") == []


def test_bus_factor_note_uses_the_email_when_there_is_no_login():
    owned = {".": ModuleOwners(".", 90, {"dev@corp.example": 6})}
    assert bus_factor_notes(owned, ["setup.py", "other/x.py"], "carol") == [
        "Only one person has changed the top-level files in the last 90 days (6 of 6 commits); a maintainer may want them to review this."
    ]


# ── hotspots ─────────────────────────────────────────────────────────────


def test_hotspots_rank_churn_times_complexity(repo):
    # path: (commits that touch it, decision points it ends with)
    plan = {
        "core.py": (4, 12),
        "api.py": (5, 6),
        "version.py": (6, 1),  # busy but simple
        "legacy.py": (1, 20),  # complex but quiet
        "notes.py": (1, 1),
        "tests/test_core.py": (6, 30),  # tests aren't hotspots
        "README.md": (6, 0),
    }
    repo.commit({"LICENSE": "x"}, day=0)
    for i in range(1, 7):
        edits = {path: branches(points) + f"# edit {i}: if and or\n" for path, (churn, points) in plan.items() if churn >= i}
        repo.commit(edits, day=i + 0.5)

    assert hotspots(repo.path, now=NOW) == [Hotspot("core.py", 4, 12, 48), Hotspot("api.py", 5, 6, 30)]
    assert hotspots(repo.path, now=NOW, top=1) == [Hotspot("core.py", 4, 12, 48)]
    # Edits 4–6 only: core.py changed once in them, api.py twice.
    assert hotspots(repo.path, now=NOW, window_days=96) == [Hotspot("api.py", 2, 6, 12, 96)]


def test_churn_follows_renames(repo):
    repo.commit({"LICENSE": "x"}, day=0)
    repo.commit({"old.py": branches(5)}, day=1)
    repo.commit({"old.py": branches(5) + "# 2\n"}, day=2)
    repo.git("mv", "old.py", "new.py", day=3)
    repo.git("commit", "-q", "-m", "rename", day=3)
    repo.commit({"new.py": branches(5) + "# 4\n"}, day=4)

    assert hotspots(repo.path, now=NOW) == [Hotspot("new.py", 4, 5, 20)]


def test_hotspot_note_and_paths():
    spots = [Hotspot("api/billing.py", 31, 140, 4340), Hotspot("api/auth.py", 12, 80, 960)]
    assert hotspot_paths(spots) == {"api/billing.py", "api/auth.py"}
    assert hotspot_note(spots, ["README.md", "api/billing.py"]) == [
        "`api/billing.py` is a hotspot: changed 31 times in the last 180 days, and among the more complex files "
        "(140 branches and conditions). Reviewed strictly."
    ]


def test_decision_points_leave_out_comments_and_strings():
    python = '"""if and or"""\nif a and b:  # or if\n    x = "if"\nelif c or d:\n    y = a // b\n'
    assert decision_points("x.py", python) == 4
    ts = "if (a && b) {} // if || while\n/* for */ const s = 'if ?';\nconst y = a ? b : c ?? d;\n"
    assert decision_points("x.ts", ts) == 4


@pytest.mark.parametrize(
    "path, source",
    [
        ("api/billing.py", True),
        ("web/src/App.tsx", True),
        ("src/Latest.java", True),
        ("tests/test_x.py", False),
        ("api/test_x.py", False),
        ("pkg/x_test.go", False),
        ("web/x.spec.ts", False),
        ("web/__tests__/x.js", False),
        ("src/main/java/UserTest.java", False),
        ("conftest.py", False),
        ("README.md", False),
        ("web/vendor/lib.js", False),
        ("web/app.min.js", False),
    ],
)
def test_what_counts_as_source(path, source):
    assert is_source(path) is source


# ── short history ────────────────────────────────────────────────────────


def test_empty_repo_and_missing_ref(repo):
    assert owners(repo.path, now=NOW) == {}
    assert hotspots(repo.path, now=NOW) == []
    repo.commit({"a/b/x.py": branches(3)}, day=1)
    assert owners(repo.path, "no-such-branch", now=NOW) == {}
    assert hotspots(repo.path, "no-such-branch", now=NOW) == []


def test_shallow_clone(repo, tmp_path):
    repo.commit({"LICENSE": "x"}, day=0)
    for day in range(1, 4):
        repo.commit({"app/core.py": branches(day)}, email=ALICE, day=day)

    # The cut-off commit shows every file as added at once, so it isn't counted.
    one = repo.clone(tmp_path / "depth1", 1)
    assert owners(one, now=NOW) == {}
    assert hotspots(one, now=NOW) == []
    two = repo.clone(tmp_path / "depth2", 2)
    assert owners(two, now=NOW) == {"app": ModuleOwners("app", 180, {"alice": 1})}
    assert hotspots(two, now=NOW) == []  # one change isn't a hotspot


def test_no_note_when_the_sole_owner_wrote_the_pr_under_an_email():
    entry = ModuleOwners("backend/billing", 180, {"dev@corp.example": 9, "other@corp.example": 1})
    owners_by_module = {"backend/billing": entry}
    assert bus_factor_notes(owners_by_module, ["backend/billing/x.py"], {"devlogin", "Dev@corp.example"}) == []
    assert bus_factor_notes(owners_by_module, ["backend/billing/x.py"], "devlogin") != []


def test_reverts_and_hotfixes_make_a_hotspot_however_simple(repo):
    repo.commit({"LICENSE": "x", "core.py": branches(12)}, day=0)
    repo.commit({"core.py": branches(12) + "# 1\n"}, day=1)
    repo.commit({"core.py": branches(12) + "# 2\n"}, day=2)
    repo.commit({"flags.py": "ON = True\n"}, day=3, message="Add flags")
    repo.commit({"flags.py": "ON = False\n"}, day=4, message="Hotfix: turn the flag off")
    repo.commit({"flags.py": "ON = True\n"}, day=5, message='Revert "Hotfix: turn the flag off"')

    found = hotspots(repo.path, now=NOW)
    assert [h.path for h in found] == ["flags.py", "core.py"]  # repeated fixes first
    assert (found[0].fixes, found[0].complex) == (2, False)
    assert hotspot_note(found, ["flags.py"]) == [
        "`flags.py` is a hotspot: changed 3 times in the last 180 days, 2 of them reverts or hotfixes. Reviewed strictly."]


def test_decision_points_skip_optional_chaining_and_count_ruby():
    assert decision_points("x.ts", "const a = b?.c ?? d;\ntype T = { x?: number };\nconst e = f ? g : h;\n") == 2  # ?? and the ternary
    assert decision_points("x.rb", "if a\nelsif b\nend\nx unless y\nuntil z do end\ncase w\nwhen 1\nend\nrescue\n") == 7
    assert decision_points("x.rb", "x = ready ? 1 : 2\ny = list.empty? ? 0 : list.first\nz = obj.nil?\n") == 2  # not nil? or empty?


def test_generated_files_are_not_hotspots():
    assert not is_source("api/types.d.ts") and not is_source("proto/user_pb2.py") and not is_source("gen/x.pb.go")
    assert not is_source("build/tool.py") and not is_source("src/__generated__/schema.ts")
    from stratum_open.repo.hotspots import generated

    assert generated("// Code generated by protoc. DO NOT EDIT.\npackage x\n")
    assert generated("var a=" + "x" * 500 + ";\n")
    assert not generated("# This module is not generated; it's written by hand.\nx = 1\n")


def test_measure_counts_lines_complexity_and_nesting():
    from stratum_open.repo.hotspots import Measure, measure

    python = ("def f(x):\n"
              "    if x and call(a,\n"
              "                  b):  # a continuation line, aligned under the bracket\n"
              "        for y in x:\n"
              "            pass\n")
    assert measure("app/x.py", python) == Measure(loc=5, complexity=4, nesting=3)
    ts = "function f(a) {\n  if (a && b) {\n    return a ? 1 : 2;\n  }\n}\n"
    assert measure("web/x.ts", ts) == Measure(loc=5, complexity=4, nesting=2)
    assert measure("tests/test_x.py", python) is None and measure("api/types.d.ts", ts) is None


def test_commit_subjects_are_read(repo):
    from stratum_open.repo.history import recent_commits

    repo.commit({"a.py": "x\n"}, day=0)
    repo.commit({"a.py": "y\n"}, day=1, message="Fix the parser\n\nA longer body.")
    assert recent_commits(repo.path, now=NOW)[0].subject == "Fix the parser"
