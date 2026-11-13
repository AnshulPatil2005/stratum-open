import importlib

import pytest

from stratum_open.diff import parse_diff
from stratum_open.policy import Policy, parse_policy
from stratum_open.repo import unknown_names
from stratum_open.repo.stdlib_names import NAMES, NEWEST
from stratum_open.repo.unknown_names import unknown_name_findings

ON = parse_policy("checks:\n  unknown_names: true\n")

CHECKOUT = {
    "app/__init__.py": "",
    "app/utils.py": "import os\n\n\ndef normalise_path(path):\n    return os.path.normpath(path)\n\n\ndef read(path):\n    return open(path).read()\n",
    "app/views.py": "import os\n",
}


def write(root, files):
    for path, text in files.items():
        full = root / path
        full.parent.mkdir(parents=True, exist_ok=True)
        full.write_text(text, encoding="utf-8")
    return str(root)


def change(path, *lines, new=False):
    """A diff of `path` from its first line: lines starting "+" are added,
    "-" removed, " " unchanged."""
    old = sum(1 for line in lines if not line.startswith("+"))
    header = [f"diff --git a/{path} b/{path}"]
    header += ["new file mode 100644", "--- /dev/null"] if new else [f"--- a/{path}"]
    header += [f"+++ b/{path}", f"@@ -{1 if old else 0},{old} +1,{len(lines) - sum(1 for line in lines if line.startswith('-'))} @@"]
    [parsed] = parse_diff("\n".join(header + list(lines)) + "\n")
    return parsed


def found(tmp_path, files, *diffs, **options):
    return unknown_name_findings(list(diffs), ON, write(tmp_path, files), **options)


def views(*added):
    """A PR adding these lines to app/views.py, under its `import os`."""
    return change("app/views.py", " import os", *[f"+{line}" for line in added])


# ── what is reported ─────────────────────────────────────────────────────


def test_importing_a_name_the_module_does_not_define_is_a_high_finding(tmp_path):
    [finding] = found(tmp_path, CHECKOUT, views("from app.utils import normalize_path, read"))

    assert (finding.path, finding.line_start, finding.line_end) == ("app/views.py", 2, 2)
    assert (finding.severity, finding.category, finding.rule, finding.source) == ("high", "bug", "code/unknown-name", "check")
    assert finding.title == "`app/utils.py` has no `normalize_path`"
    assert finding.detail == (
        "`app.utils` is `app/utils.py`, which defines no top-level `normalize_path`. This line imports it, so it raises "
        "ImportError as soon as this file is imported. Did you mean `normalise_path`?"
    )
    assert finding.suggestion == "Use `normalise_path`."


def test_a_module_attribute_that_is_not_there_is_a_medium_finding(tmp_path):
    for bind, use in (
        ("from app import utils", "utils.normalize_path(p)"),
        ("import app.utils", "app.utils.normalize_path(p)"),
        ("import app.utils as helpers", "helpers.normalize_path(p)"),
        ("from . import utils", "print(utils.normalize_path)"),
    ):
        [finding] = found(tmp_path, CHECKOUT, views(bind, "", "def show(p):", f"    return {use}"))
        assert (finding.line_start, finding.severity, finding.title) == (5, "medium", "`app/utils.py` has no `normalize_path`"), bind
        assert "raises AttributeError when this line runs. Did you mean `normalise_path`?" in finding.detail


def test_the_import_may_already_be_there_when_the_pr_adds_the_call(tmp_path):
    files = CHECKOUT | {"app/views.py": "from app import utils\n\n\ndef show(p):\n    return utils.read(p)\n"}
    diff = change("app/views.py", " from app import utils", " ", " ", " def show(p):", "-    return utils.read(p)", "+    return utils.read_text(p)")
    [finding] = found(tmp_path, files, diff)
    assert (finding.line_start, finding.title) == (5, "`app/utils.py` has no `read_text`")
    assert "`utils` is `app/utils.py`" in finding.detail


def test_relative_imports_are_followed(tmp_path):
    files = CHECKOUT | {"app/core/__init__.py": "", "app/core/jobs.py": ""}
    diff = change("app/core/jobs.py", "+from ..utils import read, write", "+from .. import utils, nothing", "+from . import jobs", new=True)
    assert [(f.line_start, f.title) for f in found(tmp_path, files, diff)] == [
        (1, "`app/utils.py` has no `write`"), (2, "`app/__init__.py` has no `nothing`"),
    ]


def test_a_script_importing_the_file_beside_it(tmp_path):
    files = {"scripts/helpers.py": "def load():\n    pass\n", "scripts/run.py": ""}
    [finding] = found(tmp_path, files, change("scripts/run.py", "+from helpers import load, dump", new=True))
    assert finding.title == "`scripts/helpers.py` has no `dump`"


def test_a_package_under_src_is_found_from_the_tests(tmp_path):
    files = {"src/app/__init__.py": "", "src/app/utils.py": "def read():\n    pass\n", "tests/test_utils.py": ""}
    [finding] = found(tmp_path, files, change("tests/test_utils.py", "+from app.utils import read, parse", new=True))
    assert finding.title == "`src/app/utils.py` has no `parse`"


def test_an_import_inside_a_function_fails_only_when_it_runs(tmp_path):
    [finding] = found(tmp_path, CHECKOUT, views("def show():", "    from app.utils import render", "    return render()"))
    assert (finding.line_start, finding.severity) == (3, "medium")
    assert "raises ImportError when this function runs." in finding.detail


def test_one_finding_for_a_name_used_on_several_lines(tmp_path):
    [finding] = found(tmp_path, CHECKOUT, views("from app import utils", "a = utils.slugify('a')", "b = utils.slugify('b')", "c = utils.read('c')"))
    assert finding.line_start == 3
    assert finding.detail.endswith("Line 4 uses it too.")


def test_the_closest_names_are_listed_when_none_is_clearly_meant(tmp_path):
    files = CHECKOUT | {"app/utils.py": "def load_json():\n    pass\n\n\ndef load_yaml():\n    pass\n\n\ndef unrelated():\n    pass\n"}
    [finding] = found(tmp_path, files, views("from app.utils import load_toml"))
    assert finding.detail.endswith("The closest names it has: `load_yaml`, `load_json`.")
    assert finding.suggestion == "Use a name `app/utils.py` defines, or add `load_toml` to it in this PR."
    [finding] = found(tmp_path, files, views("from app.utils import connect"))
    assert finding.detail.endswith("as soon as this file is imported.")


def test_the_module_is_read_as_the_pr_leaves_it(tmp_path):
    adds = change("app/utils.py", " import os", "+", "+def normalize_path(path):", "+    return path")
    assert found(tmp_path, CHECKOUT, adds, views("from app.utils import normalize_path")) == []

    removes = change("app/utils.py", " import os", " ", " ", "-def normalise_path(path):", "-    return os.path.normpath(path)", "+def clean(path):", "+    return path")
    [finding] = found(tmp_path, CHECKOUT, removes, views("from app.utils import normalise_path"))
    assert finding.title == "`app/utils.py` has no `normalise_path`"


def test_a_file_the_pr_adds_is_a_module_like_any_other(tmp_path):
    new = change("app/paths.py", "+def join(*parts):", "+    return '/'.join(parts)", new=True)
    findings = found(tmp_path, CHECKOUT, new, views("from app.paths import join, split", "from app import paths"))
    assert [f.title for f in findings] == ["`app/paths.py` has no `split`"]


def test_the_callers_own_text_of_a_file_is_used_when_it_agrees_with_the_diff(tmp_path):
    # The checkout has moved on, so the diff no longer applies to it.
    files = CHECKOUT | {"app/views.py": "import sys\nimport os\n"}
    diff = views("from app.utils import normalize_path")
    assert found(tmp_path, files, diff) == []
    head ="import os\nfrom app.utils import normalize_path\n"
    assert len(found(tmp_path, files, diff, contents={"app/views.py": head})) == 1
    assert found(tmp_path, files, diff, contents={"app/views.py": "import os\nimport sys\n"}) == []


def test_a_checkout_that_already_holds_the_change(tmp_path):
    files = CHECKOUT | {"app/views.py": "import os\nfrom app.utils import normalize_path\n\nVALUE = 1\n"}
    diff = change("app/views.py", " import os", "+from app.utils import normalize_path", " ", " VALUE = 1")
    assert found(tmp_path, files, diff) == []  # read as the default branch, the diff doesn't apply
    assert len(found(tmp_path, files, diff, applied=True)) == 1


# ── the standard library ─────────────────────────────────────────────────


def test_a_standard_library_name_no_python_has(tmp_path):
    findings = found(tmp_path, CHECKOUT, views("import json", "from shutil import copy_tree", "os.path.makedirs('a')", "data = json.load_string('{}')"))
    assert [(f.line_start, f.severity, f.title) for f in findings] == [
        (3, "high", "`shutil` has no `copy_tree`"),
        (4, "medium", "`os.path` has no `makedirs`"),
        (5, "medium", "`json` has no `load_string`"),
    ]
    assert findings[0].detail.startswith(f"The standard library's `shutil` has no `copy_tree`: not in Python 3.8 to 3.{NEWEST}, and not in the Python this review ran on.")
    assert findings[0].detail.endswith("Did you mean `copytree`?")
    assert findings[1].detail.endswith("`os.path.makedirs` raises AttributeError when this line runs. `os.makedirs` does exist.")


def test_a_name_from_another_python_version_or_platform_is_left_alone(tmp_path):
    lines = [
        "import shutil, subprocess, string, tomllib",
        "os.path.isjunction('a'), os.path.isreserved('a'), os.path.splitroot('a')",  # 3.12 and 3.13, one of them Windows only
        "shutil.ExecError, string.templatelib, tomllib.loads",  # gone in 3.16, new in 3.14, new in 3.11
        "subprocess.STARTUPINFO, subprocess.CREATE_NO_WINDOW",  # Windows only
        "from json import tool, JSONDecodeError",
    ]
    assert found(tmp_path, CHECKOUT, views(*lines)) == []


def test_a_standard_library_constant_is_left_alone(tmp_path):
    # Another package may have added it (`logging.TRACE`), and a build may lack one.
    assert found(tmp_path, CHECKOUT, views("import logging, subprocess", "logging.log(logging.TRACE, 'x')", "from subprocess import CREATE_NEW_THING")) == []


def test_a_standard_library_module_whose_names_cannot_be_listed_is_left_alone(tmp_path):
    assert "os" not in NAMES and "re" not in NAMES and "socket" not in NAMES
    assert found(tmp_path, CHECKOUT, views("import re, socket", "os.make_dirs('a')", "re.find('a', 'b')", "from socket import nothing")) == []


def test_the_list_has_every_name_the_running_python_has():
    # The list was read from source; the interpreter is the second opinion. They must agree.
    for module, names in NAMES.items():
        try:
            live = importlib.import_module(module)
        except ModuleNotFoundError:
            continue  # newer than this Python
        missing = [name for name in dir(live) if name not in names and not (name.startswith("__") and name.endswith("__"))]
        assert missing == [], f"{module}: run scripts/stdlib_names.py again, or see why it reads this module differently"


def test_the_running_python_is_asked_too(tmp_path, monkeypatch):
    # A name newer than the list, on a Python that has it.
    monkeypatch.setitem(unknown_names.STDLIB, "json", NAMES["json"] - {"dumps"})
    assert found(tmp_path, CHECKOUT, views("import json", "json.dumps({})")) == []


def test_a_file_named_like_a_standard_library_module_makes_it_uncertain(tmp_path):
    files = CHECKOUT | {"app/json.py": "def load_string(text):\n    pass\n"}
    assert found(tmp_path, files, views("import json", "json.load_string('{}')")) == []


def test_a_project_on_a_python_newer_than_the_list_is_left_alone(tmp_path):
    line = views("import json", "json.load_string('{}')")
    assert len(found(tmp_path / "older", CHECKOUT | {"pyproject.toml": '[project]\nrequires-python = ">=3.9"\n'}, line)) == 1
    for number, (path, text) in enumerate((
        ("pyproject.toml", '[project]\nrequires-python = ">=3.99"\n'),
        (".python-version", "3.99\n"),
        (".github/workflows/tests.yml", "jobs:\n  test:\n    strategy:\n      matrix:\n        python-version:\n          - '3.12'\n          - '3.99'\n"),
        ("tox.ini", "[tox]\nenvlist = py312, py399\n"),
    )):
        assert found(tmp_path / str(number), CHECKOUT | {path: text}, line) == [], path


# ── what is left alone ───────────────────────────────────────────────────


def test_off_unless_the_policy_turns_it_on(tmp_path):
    repo = write(tmp_path, CHECKOUT)
    diff = views("from app.utils import normalize_path")
    assert not Policy().checks.unknown_names
    assert unknown_name_findings([diff], Policy(), repo) == []
    assert unknown_name_findings([diff], parse_policy("checks:\n  heuristics: true\n"), repo) == []
    assert len(unknown_name_findings([diff], ON, repo)) == 1


def test_without_a_checkout_nothing_is_said(tmp_path):
    assert unknown_name_findings([views("from app.utils import normalize_path")], ON, None) == []


def test_packages_from_outside_the_repository_are_not_checked(tmp_path):
    assert found(tmp_path, CHECKOUT, views("import requests", "from flask import nothing", "requests.get_json('u')")) == []


def test_a_module_that_is_not_in_the_checkout_is_never_reported(tmp_path):
    # It may be written by the build.
    assert found(tmp_path, CHECKOUT, views("from app._version import version", "from app.missing import thing", "import app.gone")) == []


@pytest.mark.parametrize("text", [
    "def __getattr__(name):\n    return load(name)\n",
    "from somewhere_else import *\n",
    "for name in ('a', 'b'):\n    globals()[name] = name\n",
    "import sys\nsetattr(sys.modules[__name__], 'x', 1)\n",
    "import sys\nsys.modules[__name__] = object()\n",
    "import lazy_loader\n__getattr__, __dir__, __all__ = lazy_loader.attach(__name__, ['a'])\n",
    "exec('normalize_path = 1')\n",
    "import apipkg\napipkg.initpkg(__name__, {})\n",
    "import importlib\nme = importlib.import_module(__name__)\n",
    "__path__ = __import__('pkgutil').extend_path(__path__, __name__)\n",
    "def broken(:\n",
])
def test_a_module_whose_names_cannot_be_listed_is_left_alone(tmp_path, text):
    files = CHECKOUT | {"app/utils.py": text}
    assert found(tmp_path, files, views("from app.utils import normalize_path", "from app import utils", "utils.other()")) == []


@pytest.mark.parametrize("text", [
    "if FAST:\n    def normalize_path(p): ...\nelse:\n    normalize_path = None\n",
    "try:\n    from fastpath import normalize_path\nexcept ImportError:\n    pass\n",
    "from typing import TYPE_CHECKING\nif TYPE_CHECKING:\n    from pathlib import Path as normalize_path\n",
    "def setup():\n    global normalize_path\n    normalize_path = str\n",
    "for normalize_path in (str,):\n    pass\n",
    "with open('x') as normalize_path:\n    pass\n",
    "class normalize_path:\n    pass\n",
    "import os.path as normalize_path\n",
    "(normalize_path := 1)\n",
    "a, (normalize_path, *rest) = 1, (2, 3)\n",
    "normalize_path: int\n",
])
def test_a_name_bound_in_any_branch_counts_as_defined(tmp_path, text):
    assert found(tmp_path, CHECKOUT | {"app/utils.py": text}, views("from app.utils import normalize_path")) == []


def test_a_shim_for_a_deprecated_name_answers_for_that_name_only(tmp_path):
    shim = "def __getattr__(name):\n    if name == 'old_read':\n        return read\n    raise AttributeError(name)\n\n\ndef read():\n    pass\n"
    findings = found(tmp_path, CHECKOUT | {"app/utils.py": shim}, views("from app.utils import old_read, older_read"))
    assert [f.title for f in findings] == ["`app/utils.py` has no `older_read`"]


def test_a_package_reexporting_from_its_submodules(tmp_path):
    files = CHECKOUT | {
        "app/__init__.py": "from .utils import normalise_path\nfrom .shapes import *\n",
        "app/shapes.py": "from .more import *\n\n\ndef circle():\n    pass\n",
        "app/more.py": "__all__ = ['square']\n\n\ndef square():\n    pass\n",
    }
    quiet = views("from app import normalise_path, circle, square, utils, shapes", "import app", "app.utils.read('a')", "app.more.square()")
    assert found(tmp_path, files, quiet) == []
    [finding] = found(tmp_path, files, views("from app import triangle"))
    assert finding.title == "`app/__init__.py` has no `triangle`"


def test_a_star_import_that_leaves_the_repository_cannot_be_followed(tmp_path):
    files = CHECKOUT | {"app/__init__.py": "from .shapes import *\n", "app/shapes.py": "from numpy import *\n"}
    assert found(tmp_path, files, views("from app import triangle")) == []


def test_star_imports_in_a_circle_end(tmp_path):
    files = CHECKOUT | {"app/a.py": "from app.b import *\nx = 1\n", "app/b.py": "from app.a import *\ny = 1\n"}
    assert found(tmp_path, files, views("from app.a import x, y", "from app.b import x, y")) == []
    assert [f.title for f in found(tmp_path, files, views("from app.a import z"))] == ["`app/a.py` has no `z`"]


def test_a_submodule_a_build_step_could_add_is_left_alone(tmp_path):
    line = views("from app import generated")
    assert len(found(tmp_path / "plain", CHECKOUT, line)) == 1
    for number, extra in enumerate(({".gitignore": "*.egg-info\n/app/generated.py\n"}, {"app/.gitignore": "gen*\n"}, {"native/speedups.c": ""},
                                    {"app/generated.pyi": ""}, {"app/generated.so": ""}, {"app/generated/data.json": ""}, {"Cargo.toml": ""})):
        assert found(tmp_path / str(number), CHECKOUT | extra, line) == [], extra


def test_a_module_python_may_load_from_another_file_is_left_alone(tmp_path):
    line = views("from app.fast import nothing", "from app import fast", "fast.nothing()")
    for number, extra in enumerate(({"app/fast.pyi": "def nothing(): ...\n"}, {"app/fast.py": "", "app/fast.pyx": ""},
                                    {"app/fast.py": "", "app/fast.cpython-312-x86_64-linux-gnu.so": ""},
                                    {"app/fast.py": "", "app/fast/__init__.py": ""}, {"app/fast/core.py": ""})):
        assert found(tmp_path / str(number), CHECKOUT | extra, line) == [], extra


def test_an_import_that_could_be_two_files_is_left_alone(tmp_path):
    line = change("tests/test_utils.py", "+from app.utils import read, parse", new=True)
    one = {"src/app/__init__.py": "", "src/app/utils.py": "def read():\n    pass\n"}
    assert len(found(tmp_path / "one", one, line)) == 1
    # The same package in two places, a package with no __init__.py, and one only some folder's code can import.
    assert found(tmp_path / "two", one | {"app/__init__.py": "", "app/utils.py": ""}, line) == []
    assert found(tmp_path / "namespace", {"src/app/utils.py": "def read():\n    pass\n"}, line) == []
    assert found(tmp_path / "elsewhere", {"services/api/app/__init__.py": "", "services/api/app/utils.py": ""}, line) == []
    assert found(tmp_path / "neighbour", one | {"tests/app.py": ""}, line) == []


def test_a_name_that_is_not_only_the_module_is_left_alone(tmp_path):
    for lines in (
        ["from app import utils", "utils = load()", "utils.normalize_path()"],
        ["from app import utils", "def show(utils):", "    return utils.normalize_path()"],
        ["from app import utils", "for utils in things:", "    utils.normalize_path()"],
        ["try:", "    import fast_utils as utils", "except ImportError:", "    from app import utils", "utils.normalize_path()"],
        ["import json", "def load(json):", "    return json.load_string()"],
        ["from other import *", "from app import utils", "utils.normalize_path()"],
        ["from app import utils", "globals()['utils'] = None", "utils.normalize_path()"],
        ["from app.utils import read", "read.cache_clear()", "self.utils.normalize_path()", "os.environ.nothing"],
    ):
        assert found(tmp_path, CHECKOUT, views(*lines)) == [], lines


def test_a_name_the_file_sets_or_tests_for_is_left_alone(tmp_path):
    for lines in (
        ["from app import utils", "utils.normalize_path = str", "utils.normalize_path('a')"],
        ["from app import utils", "if hasattr(utils, 'normalize_path'):", "    utils.normalize_path('a')"],
        ["from app import utils", "ok = getattr(utils, 'normalize_path', None) and utils.normalize_path('a')"],
        ["from app import utils", "monkeypatch.setattr(utils, 'normalize_path', str, raising=False)", "utils.normalize_path('a')"],
    ):
        assert found(tmp_path, CHECKOUT, views(*lines)) == [], lines


def test_guarded_lines_are_left_alone(tmp_path):
    for lines in (
        ["try:", "    from app.utils import normalize_path", "except ImportError:", "    normalize_path = None"],
        ["from app import utils", "try:", "    utils.normalize_path('a')", "except AttributeError:", "    pass"],
        ["from contextlib import suppress", "from app import utils", "with suppress(AttributeError):", "    utils.normalize_path('a')"],
        ["from typing import TYPE_CHECKING", "if TYPE_CHECKING:", "    from app.utils import NormalizedPath"],
        ["from app import utils", "def show():", "    if not hasattr(utils, 'fast'):", "        return None", "    from app.utils import fast_path"],
        # A test that the name is missing.
        ["import pytest", "from app import utils", "def test_missing():", "    with pytest.raises(AttributeError):", "        utils.normalize_path"],
        ["from app import utils", "def test_missing(self):", "    self.assertRaises(AttributeError, lambda: utils.normalize_path)"],
        ["import pytest", "@pytest.mark.xfail(reason='not written yet')", "def test_later():", "    from app.utils import normalize_path"],
        ["from app import utils", "def show(path: utils.NormalizedPath) -> utils.NormalizedPath:", "    kept: utils.NormalizedPath = path", "    return kept"],
        ["from app import utils", "name = utils.__name__, utils.__file__, utils.__doc__"],
    ):
        assert found(tmp_path, CHECKOUT, views(*lines)) == [], lines


def test_a_standard_library_name_under_a_version_test_is_left_alone(tmp_path):
    # It may be a name from a Python newer than the list.
    for lines in (
        ["import sys", "if sys.version_info >= (3, 99):", "    from json import load_string", "else:", "    from json import older_load_string"],
        ["import json", "from compat import PY399", "if PY399:", "    json.load_string('')"],
        ["import json", "from compat import PY399", "def load():", "    if not PY399:", "        return None", "    return json.load_string('')"],
        ["import json, os", "loads = json.load_string if os.name == 'nt' else json.loads"],
        ["import json", "from compat import PY399", "@skipif(not PY399, reason='new')", "def test_new():", "    json.load_string('')"],
        ["import json, sys", "pytestmark = skipif(sys.version_info < (3, 99))", "def test_new():", "    json.load_string('')"],
    ):
        assert found(tmp_path, CHECKOUT, views(*lines)) == [], lines


def test_a_version_test_does_not_excuse_a_name_missing_from_the_projects_own_module(tmp_path):
    # Whatever the Python or platform, the module is this one file.
    lines = ["import sys", "from app import utils", "if sys.platform == 'win32' or utils.FAST:", "    utils.normalize_path('a')"]
    assert [(f.line_start, f.title) for f in found(tmp_path, CHECKOUT, views(*lines))] == [
        (4, "`app/utils.py` has no `FAST`"), (5, "`app/utils.py` has no `normalize_path`"),
    ]


def test_a_type_checking_block_does_not_hide_what_follows_it(tmp_path):
    lines = ["from typing import TYPE_CHECKING", "if TYPE_CHECKING:", "    from app.utils import Path", "from app.utils import normalize_path"]
    assert [f.line_start for f in found(tmp_path, CHECKOUT, views(*lines))] == [5]


def test_an_except_block_is_not_a_guard(tmp_path):
    lines = ["try:", "    import fast", "except ImportError:", "    from app.utils import normalize_path"]
    assert len(found(tmp_path, CHECKOUT, views(*lines))) == 1


def test_the_error_a_test_expects_is_a_name_like_any_other(tmp_path):
    lines = ["import pytest", "from app import utils", "def test_reading():", "    with pytest.raises(utils.ReadError):", "        utils.read_all('a')"]
    assert [(f.line_start, f.title) for f in found(tmp_path, CHECKOUT, views(*lines))] == [(5, "`app/utils.py` has no `ReadError`")]


def test_a_name_set_from_another_file_is_left_alone(tmp_path):
    line = views("from app import utils", "utils.normalize_path('a')", "from app.utils import other_name")
    assert len(found(tmp_path / "plain", CHECKOUT, line)) == 2
    for number, patch in enumerate((
        "from app import utils\nutils.normalize_path = str\nutils.other_name: str = ''\n",
        "import app.utils\nsetattr(app.utils, 'normalize_path', str)\nsetattr(app.utils, \"other_name\", str)\n",
        "def test(monkeypatch):\n    monkeypatch.setattr(utils, 'normalize_path', str, raising=False)\n    with mock.patch.object(utils, 'other_name', create=True):\n        pass\n",
        # The module handed to something that sets names it works out as it goes.
        "from app import utils\nfor name in NAMES:\n    setattr(utils, name, str)\n",
        "import app.utils\nvars(app.utils).update(EXTRA)\n",
        "import app\napp.utils.__dict__.update(EXTRA)\n",
    )):
        assert found(tmp_path / str(number), CHECKOUT | {"tests/conftest.py": patch}, line) == [], patch
    # Its name in a list of exports sets nothing.
    assert len(found(tmp_path / "all", CHECKOUT | {"app/api.py": "__all__ = ['normalize_path', 'other_name']\n"}, line)) == 2


def test_only_added_lines_are_reported(tmp_path):
    files = CHECKOUT | {"app/views.py": "import os\nfrom app.utils import normalize_path\n"}
    diff = change("app/views.py", " import os", " from app.utils import normalize_path", "+from app.utils import read")
    assert found(tmp_path, files, diff) == []


def test_a_file_that_starts_with_a_byte_order_mark_is_read(tmp_path):
    files = CHECKOUT | {"app/utils.py": chr(0xFEFF) + "def read(path):\n    pass\n"}
    assert [f.title for f in found(tmp_path, files, views("from app.utils import read, normalize_path"))] == ["`app/utils.py` has no `normalize_path`"]


def test_files_that_cannot_be_read_as_python_are_left_alone(tmp_path):
    assert found(tmp_path, CHECKOUT, views("from app.utils import normalize_path", "def broken(:")) == []
    assert found(tmp_path, CHECKOUT, change("notes/views.txt", "+from app.utils import normalize_path", new=True)) == []
    assert found(tmp_path, CHECKOUT, change("app/.hidden/views.py", "+from app.utils import normalize_path", new=True)) == []
    assert found(tmp_path, CHECKOUT, change("vendor/views.py", "+from app.utils import normalize_path", new=True)) == []


def test_a_checkout_too_big_to_list_says_nothing(tmp_path, monkeypatch):
    diff = views("from app.utils import normalize_path")
    monkeypatch.setattr(unknown_names, "MAX_FILES", 2)
    assert found(tmp_path, CHECKOUT, diff) == []
    monkeypatch.setattr(unknown_names, "MAX_FILES", 20_000)
    monkeypatch.setattr(unknown_names, "MAX_PARSED", 0)
    assert found(tmp_path, CHECKOUT, diff) == []
    monkeypatch.setattr(unknown_names, "MAX_PARSED", 400)
    monkeypatch.setattr(unknown_names, "MAX_PR_FILES", 1)
    assert found(tmp_path, CHECKOUT, diff) == []


def test_an_error_becomes_a_low_finding_and_never_raises(tmp_path, monkeypatch):
    def broken(*args):
        raise RuntimeError("no such luck")

    monkeypatch.setattr(unknown_names, "_check", broken)
    [finding] = found(tmp_path, CHECKOUT, views("from app.utils import normalize_path"))
    assert (finding.severity, finding.rule, finding.source) == ("low", "checks/error", "check")
    assert finding.title == "The unknown names check didn't run"
    assert "RuntimeError: no such luck" in finding.detail
