"""Writes stratum_open/repo/stdlib_names.py: every name a short list of
standard-library modules has had, in each CPython release it's run on.

The unknown-names check reports `json.load_string` only if no Python the
project could run has it, and one interpreter can't say that. So the names
are read from CPython's own sources, one checkout per release, with the same
rules the check uses on a repository: a name bound in any branch counts, and
a module whose names can't be listed from its text is left out for good.

    for v in 3.8 3.9 3.10 3.11 3.12 3.13 3.14 3.15; do
        git clone --depth 1 --branch $v https://github.com/python/cpython cpython/$v
    done
    python scripts/stdlib_names.py cpython

Run it again when a Python is released, after adding the release to VERSIONS.
A module is dropped, with the reason printed, when any release's source can't
be listed or doesn't parse on the Python running this. Nothing in the
checkouts is imported or run.
"""

from __future__ import annotations

import ast
import re
import sys
import textwrap
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent.parent))

from stratum_open.repo.unknown_names import _Repo  # noqa: E402

VERSIONS = ("3.8", "3.9", "3.10", "3.11", "3.12", "3.13", "3.14", "3.15")
OUT = Path(__file__).resolve().parent.parent / "stratum_open" / "repo" / "stdlib_names.py"

# Modules that are safe to import (the check also asks the running
# interpreter) and common enough to be worth it. The ones that can't be
# listed drop out below, so this can name more than ends up in the file.
MODULES = (
    "argparse", "ast", "base64", "bisect", "calendar", "collections", "collections.abc", "configparser", "contextlib", "copy",
    "csv", "dataclasses", "datetime", "decimal", "difflib", "enum", "fnmatch", "fractions", "functools", "getpass", "glob",
    "gzip", "hashlib", "heapq", "hmac", "html", "http.client", "inspect", "io", "ipaddress", "json", "keyword", "linecache",
    "logging", "logging.config", "logging.handlers", "mimetypes", "numbers", "operator", "os", "os.path", "pathlib", "pickle",
    "platform", "pprint", "queue", "random", "re", "secrets", "shlex", "shutil", "signal", "socket", "sqlite3", "statistics",
    "string", "struct", "subprocess", "tarfile", "tempfile", "textwrap", "threading", "timeit", "tomllib", "traceback", "types",
    "typing", "unittest", "unittest.mock", "urllib.error", "urllib.parse", "urllib.request", "uuid", "warnings", "weakref",
    "zipfile",
)
# `os.path` is one of these two, by platform. `collections.abc` was a file of its own
# until 3.13, which makes it another name for `_collections_abc`, private helpers and all.
ALIASES = {"os.path": ("posixpath", "ntpath"), "collections.abc": ("collections.abc", "_collections_abc")}
_LAZY = re.compile(r"^(\s*)lazy\s+(import|from)\b", re.M)


class _Lib(_Repo):
    """One release's Lib/ folder."""

    def text(self, path: str) -> str | None:
        # `lazy import` (3.15) binds the same names as `import`, and doesn't parse on an older Python.
        text = super().text(path)
        return _LAZY.sub(r"\1\2", text) if text else text


def main() -> int:
    root = Path(sys.argv[1])
    repos = {version: _Lib(str(root / version / "Lib"), [], {}, False, stdlib=True) for version in VERSIONS}
    names: dict[str, set[str]] = {}
    for module in MODULES:
        found, why = _names(module, repos)
        if found is None:
            print(f"left out: {module} ({why})")
        else:
            names[module] = found
    lines = [
        '"""Every top-level name each of these standard-library modules has had, in',
        f"CPython {VERSIONS[0]} to {VERSIONS[-1]}, submodules included. Written by scripts/stdlib_names.py",
        "from CPython's sources; don't edit it by hand.",
        "",
        "A module is here only if its names can be listed from its source in every",
        'one of those releases. See repo/unknown_names.py for how it\'s used."""',
        "",
        f"NEWEST = {VERSIONS[-1].split('.')[1]}  # the last Python 3 minor release read",
        "",
        "NAMES: dict[str, frozenset[str]] = {",
    ]
    for module in sorted(names):
        body = textwrap.fill(" ".join(sorted(names[module])), width=132, initial_indent=" " * 8, subsequent_indent=" " * 8,
                             break_long_words=False, break_on_hyphens=False)
        lines += [f'    "{module}": frozenset("""', body, '    """.split()),']
    lines.append("}")
    OUT.write_text("\n".join(lines) + "\n", encoding="utf-8", newline="\n")
    print(f"{len(names)} modules, {sum(map(len, names.values()))} names -> {OUT}")
    return 0


def _names(module: str, repos: dict[str, _Repo]) -> tuple[set[str] | None, str]:
    found: set[str] = set()
    seen = False
    for version, repo in repos.items():
        for real in ALIASES.get(module, (module,)):
            parts = real.split(".")
            path = repo.file_in("", parts)
            if path is None:
                if parts[-1] in repo.siblings("/".join(parts[:-1])):
                    return None, f"not one Python file in {version}"
                continue  # not in this release
            summary = repo.module(path)
            if summary is None:
                return None, f"{path} doesn't parse in {version}"
            if summary.open:
                return None, f"its names can't be listed in {version}: {summary.open}"
            # A second opinion from the module itself: what it exports must be among the names found.
            unbound = sorted(_exported(repo.text(path) or "") - summary.names)
            if unbound:
                return None, f"it exports {', '.join(unbound[:3])} in {version}, which its text doesn't bind"
            seen = True
            found |= summary.names
            if path.endswith("/__init__.py"):
                found |= repo.siblings(path.rpartition("/")[0]) - {"__init__", "__main__", "__pycache__"}
    return (found, "") if seen else (None, "not found")


def _exported(text: str) -> set[str]:
    """Every string in a statement that sets or extends `__all__`."""
    found: set[str] = set()
    for node in ast.walk(ast.parse(text)):
        if isinstance(node, (ast.Assign, ast.AugAssign, ast.AnnAssign, ast.Expr)) and "__all__" in ast.dump(node)[:400]:
            found.update(n.value for n in ast.walk(node) if isinstance(n, ast.Constant) and isinstance(n.value, str))
    return found


if __name__ == "__main__":
    raise SystemExit(main())
