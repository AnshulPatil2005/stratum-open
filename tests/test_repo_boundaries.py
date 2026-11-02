from stratum_open.diff import parse_diff
from stratum_open.policy import Policy, parse_policy
from stratum_open.repo.boundaries import boundary_findings

POLICY = parse_policy(
    """
boundaries:
  modules:
    api: ["server/api/**", "web/api/**"]
    db: ["server/db/**", "web/db/**"]
    core: ["server/core/**"]
  forbid: ["api -> db"]
"""
)

CHECKOUT = {
    "server/__init__.py": "",
    "server/api/__init__.py": "",
    "server/api/views.py": "import os\n",
    "server/db/__init__.py": "",
    "server/db/models.py": "",
    "server/db/session.py": "",
    "server/core/__init__.py": "",
    "server/core/config.py": "",
    "web/api/client.ts": "",
    "web/db/store.ts": "",
}


def write(root, files):
    for path, text in files.items():
        full = root / path
        full.parent.mkdir(parents=True, exist_ok=True)
        full.write_text(text, encoding="utf-8")
    return str(root)


def change(path, *lines, new=False):
    """A diff of `path`: lines starting "+" are added, " " unchanged."""
    kept = sum(1 for line in lines if line.startswith(" "))
    header = [f"diff --git a/{path} b/{path}"]
    header += ["new file mode 100644", "--- /dev/null"] if new else [f"--- a/{path}"]
    header += [f"+++ b/{path}", f"@@ -{1 if kept else 0},{kept} +1,{len(lines)} @@"]
    [parsed] = parse_diff("\n".join(header + list(lines)) + "\n")
    return parsed


def test_forbidden_import_is_a_finding_and_an_allowed_one_is_not(tmp_path):
    repo = write(tmp_path, CHECKOUT)
    diff = change("server/api/views.py", " import os", "+from server.db.models import User", "+from server.core import config")

    [finding] = boundary_findings([diff], POLICY, repo)
    assert (finding.path, finding.line_start, finding.line_end) == ("server/api/views.py", 2, 2)
    assert (finding.severity, finding.category, finding.rule, finding.source) == ("medium", "maintainability", "boundaries/forbidden", "check")
    assert finding.title == "`api` must not import `db`"
    assert finding.detail == (
        "`api` must not import `db` (rule `api -> db` in .stratum/policy.yml). "
        "This line imports `server/db/models.py`, which is in `db`."
    )
    assert finding.confidence == 0.9


def test_relative_and_multiline_imports(tmp_path):
    repo = write(tmp_path, CHECKOUT)
    python = change("server/api/jobs.py", "+import os", "+from ..db import session, models", new=True)
    js = change("web/api/client.ts", " import {", "   get,", "+} from '../db/store'")

    findings = boundary_findings([python, js], POLICY, repo)
    assert [(f.path, f.line_start) for f in findings] == [("server/api/jobs.py", 2), ("web/api/client.ts", 3)]


def test_imports_the_pr_did_not_add_are_left_alone(tmp_path):
    repo = write(tmp_path, CHECKOUT)
    diff = change("server/api/views.py", " from server.db.models import User", "+x = 1")

    assert boundary_findings([diff], POLICY, repo) == []


def test_imports_of_what_the_checkout_does_not_have(tmp_path):
    repo = write(tmp_path, CHECKOUT)
    added = change("server/db/fresh.py", "+x = 1", new=True)
    diff = change(
        "server/api/views.py", " import os",
        "+from server.db.fresh import x",  # added by this PR: found
        "+from server.db.missing import y",  # nowhere: no guessing when there's a checkout
        "+import db",  # not on any root
    )

    findings = boundary_findings([added, diff], POLICY, repo)
    assert [(f.line_start, f.detail.split("This line imports ")[1]) for f in findings] == [
        (2, "`server/db/fresh.py`, which is in `db`."),
    ]


def test_without_a_checkout_the_import_text_is_used(tmp_path):
    diff = change(
        "server/api/views.py", " import os",
        "+from server.db.models import User",
        "+from db import session",  # as run from server/
        "+import requests",
        "+from server.core import config",
    )
    js = change("web/api/client.ts", "+import { save } from '../db/store'", new=True)

    findings = boundary_findings([diff, js], POLICY, None)
    assert [(f.path, f.line_start, f.confidence) for f in findings] == [
        ("server/api/views.py", 2, 0.6), ("server/api/views.py", 3, 0.6), ("web/api/client.ts", 1, 0.6),
    ]
    assert findings[0].detail.endswith("This line imports `server.db.models`, which is in `db`.")


def test_only_files_in_a_module_with_a_rule_are_checked(tmp_path):
    repo = write(tmp_path, CHECKOUT)
    from_db = change("server/db/models.py", "+from server.api import views", new=True)  # no `db -> api` rule
    from_core = change("server/core/config.py", "+from server.db import models", new=True)  # core has no rule
    outside = change("scripts/seed.py", "+from server.db import models", new=True)
    within = change("server/api/views.py", " import os", "+from server import api")

    assert boundary_findings([from_db, from_core, outside, within], POLICY, repo) == []
    assert boundary_findings([within], Policy(), "/no/such/checkout") == []
