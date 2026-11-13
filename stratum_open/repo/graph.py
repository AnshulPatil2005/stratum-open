"""Which files import a changed file, from the Python and JS/TS in a checkout.

A file many others import is worth a closer look: a change there reaches all
of them. Imports are found with regular expressions, not a parser, and each
is resolved to a file in the repo the way Python or Node would look for it:

- Python: `import a.b` and `from a.b import c` are tried from the repo root,
  from `src/`, and from the top-level folder the importer is in when that
  folder holds packages without being one (`backend/` holding
  `backend/app/__init__.py`, imported as `app`). Relative imports
  (`from ..db import x`) are resolved against the importer's package. Standard
  library names are never resolved, even when a repo file shares one.
- JS/TS: only relative specifiers (`./x`, `../x`), tried as written, with
  `.ts/.tsx/.js/.jsx/.mjs/.cjs/.mts/.cts`, as `.ts` for a `.js` specifier, and
  as a folder's `index.*`.

Anything else is a package from outside the repo and is ignored. An import is
never matched to a file by its name alone: a wrong edge is worse than a
missing one.

Comments, Python's triple-quoted strings and imports written inside JS
strings are left out. A Python import that doesn't start its line
(`if x: import y`) is missed.

Limits: tsconfig `paths` aliases (`@/x`), `package.json` exports, workspace
packages, `sys.path` tweaks, a script importing its own folder, Python
packages deeper than one folder under the root (`services/x/pkg/`), `.vue`
and `.svelte` files, and imports built at run time aren't followed. Folders
named `node_modules`, `dist`, `build`, `vendor`, `__pycache__`, `venv`,
`site-packages` or starting with a dot are skipped, as are files over 1 MB,
minified files and symlinks. The scan stops at 20,000 source files, so on
very large repos counts are lower bounds.
"""

from __future__ import annotations

import bisect
import os
import posixpath
import re
import sys
from concurrent.futures import ThreadPoolExecutor
from dataclasses import dataclass, field
from typing import Iterable

PY_EXTENSIONS = (".py",)
JS_EXTENSIONS = (".ts", ".tsx", ".js", ".jsx", ".mjs", ".cjs", ".mts", ".cts")
SKIP_DIRS = frozenset({"node_modules", "dist", "build", "vendor", "__pycache__", "venv", "site-packages"})
MAX_FILES = 20_000
MAX_BYTES = 1_000_000
_SHOWN = 3  # example importers named in a note

# TypeScript ESM imports `./x.js` and means `x.ts`.
_TS_FOR_JS = {".js": (".ts", ".tsx"), ".jsx": (".tsx",), ".mjs": (".mts",), ".cjs": (".cts",)}

_PY_KEYWORD = re.compile(r"import\b")
_PY_IMPORT = re.compile(r"^[ \t]*import[ \t]+([^\n;#]+)", re.M)
_PY_FROM = re.compile(r"^[ \t]*from[ \t]+(\.*)[ \t]*([\w.]*)[ \t]+import[ \t]*(\([^)]*\)|[^\n;#]*)", re.M)
_PY_NAME = re.compile(r"[\w.]+")

_JS_COMMENT_OR_STRING = re.compile(
    r"""//[^\n]*|/\*[\s\S]*?\*/|'(?:\\.|[^'\\\n])*'|"(?:\\.|[^"\\\n])*"|`(?:\\.|[^`\\])*`"""
)
# One pattern, starting with the keyword so the regex engine can skip ahead to
# it; the character before it is checked in code.
_JS_IMPORT = re.compile(
    r"""(?:import|export)\s+[\w$*{},\s]*?\bfrom\s*(['"])([^'"\n]+)\1"""  # import x from '…', export … from '…'
    r"""|import\s*(['"])([^'"\n]+)\3"""  # import '…'
    r"""|(?:require|import)\s*\(\s*(['"`])([^'"`\n$]+)\5\s*\)"""  # require('…'), import('…')
)


@dataclass(frozen=True)
class Import:
    line: int  # the line that names the module
    module: str  # dotted for Python ("a.b"), the specifier for JS/TS ("../a/b")
    names: tuple[str, ...] = ()  # Python's `from module import names`
    level: int = 0  # Python's leading dots


@dataclass
class Importers:
    count: int
    examples: list[str] = field(default_factory=list)
    imports: list[str] = field(default_factory=list)  # the repo files this one imports, the first few


def importers(repo_dir: str, paths: Iterable[str], *, limit: int = 5) -> dict[str, Importers]:
    """For each path, how many files in the checkout import it and the first
    `limit` of them, alphabetically, and the first `limit` repo files it
    imports. A path nothing imports has a count of 0."""
    found: dict[str, set[str]] = {path: set() for path in paths}
    outgoing: dict[str, set[str]] = {path: set() for path in found}
    if not any(path.endswith(PY_EXTENSIONS + JS_EXTENSIONS) for path in found):
        return {path: Importers(0) for path in found}
    files = code_files(repo_dir)
    resolver = Resolver(files)
    # Reading is most of the time, and threads overlap it; batches keep only
    # a few hundred files in memory at once.
    with ThreadPoolExecutor(max_workers=8) as pool:
        for at in range(0, len(files), 256):
            batch = files[at:at + 256]
            for path, text in zip(batch, pool.map(lambda p: _read(repo_dir, p), batch)):
                if text is None or _minified(text):
                    continue
                for imp in parse_imports(path, text):
                    for target in resolver.resolve(path, imp):
                        if target in found:
                            found[target].add(path)
                        if path in outgoing and target != path:
                            outgoing[path].add(target)
    return {path: Importers(len(by), sorted(by)[:limit], sorted(outgoing[path])[:limit]) for path, by in found.items()}


def reach_note(importers_by_path: dict[str, Importers]) -> list[str]:
    """One line for each file at least 3 others import, most imported first."""
    notes = []
    for path, found in sorted(importers_by_path.items(), key=lambda item: (-item[1].count, item[0])):
        if found.count >= 3:
            shown = ", ".join(f"`{example}`" for example in found.examples[:_SHOWN])
            notes.append(f"`{path}` is imported by {found.count} files" + (f" (e.g. {shown})." if shown else "."))
    return notes


def related_note(found: Importers) -> str:
    """For one file's own prompt: who imports it and what it imports, so the
    model knows which callers a change can break."""
    parts = []
    if found.count:
        shown = ", ".join(f"`{example}`" for example in found.examples)
        parts.append(f"Imported by {found.count} file{'' if found.count == 1 else 's'}: {shown}"
                     + (" and others." if found.count > len(found.examples) else "."))
    if found.imports:
        parts.append("It imports " + ", ".join(f"`{target}`" for target in found.imports) + ".")
    return " ".join(parts)


def code_files(repo_dir: str) -> list[str]:
    """Python and JS/TS files in the checkout, as repo-relative posix paths."""
    found: list[str] = []
    for folder, dirs, names in os.walk(repo_dir):
        dirs[:] = sorted(d for d in dirs if d not in SKIP_DIRS and not d.startswith("."))
        rel = os.path.relpath(folder, repo_dir).replace(os.sep, "/")
        for name in sorted(names):
            if name.endswith(PY_EXTENSIONS + JS_EXTENSIONS):
                found.append(name if rel == "." else f"{rel}/{name}")
                if len(found) >= MAX_FILES:
                    return found
    return found


def parse_imports(path: str, text: str) -> list[Import]:
    """Every import in a Python or JS/TS file's text; other files have none."""
    if path.endswith(PY_EXTENSIONS):
        return _python_imports(text)
    if path.endswith(JS_EXTENSIONS):
        return _js_imports(text)
    return []


class Resolver:
    """Finds the repo files an import names, among `files`.

    `roots` are the top-level folders Python code inside them may import from
    as if they were the root. By default, those holding a package without
    being one; pass them when there's no checkout to look at.
    """

    def __init__(self, files: Iterable[str], roots: Iterable[str] | None = None):
        self.files = set(files)
        if roots is None:
            packages = {path.rpartition("/")[0] for path in self.files if path.endswith("/__init__.py")}
            roots = {p.partition("/")[0] for p in packages if p.count("/") == 1} - packages
        self.roots = set(roots)

    def resolve(self, importer: str, imp: Import) -> list[str]:
        """The files the import loads; none when it names nothing in the repo."""
        found: list[str] = []
        for group in self.candidates(importer, imp):
            hit = next((path for path in group if path in self.files), None)
            if hit and hit != importer and hit not in found:
                found.append(hit)
        return found

    def candidates(self, importer: str, imp: Import) -> list[list[str]]:
        """For each thing the import loads, the paths it could be, in the
        order Python or Node would try them."""
        if importer.endswith(PY_EXTENSIONS):
            return self._python(importer, imp)
        if importer.endswith(JS_EXTENSIONS):
            return self._js(importer, imp)
        return []

    def _python(self, importer: str, imp: Import) -> list[list[str]]:
        if imp.level:
            package = importer.split("/")[:-1]
            keep = len(package) - (imp.level - 1)
            if keep < 0:
                return []  # more dots than folders
            bases = ["/".join(package[:keep])]
        elif not imp.module or imp.module.split(".")[0] in sys.stdlib_module_names:
            return []
        else:
            top = importer.partition("/")[0] if "/" in importer else ""
            bases = list(dict.fromkeys(([top] if top in self.roots else []) + ["", "src"]))
        module = imp.module.replace(".", "/")
        heads = [posixpath.join(base, module) if module else base for base in bases]

        def own(head: str) -> list[str]:
            return [f"{head}/__init__.py", f"{head}.py"] if module else [posixpath.join(head, "__init__.py")]

        if not imp.names:
            return [[path for head in heads for path in own(head)]]
        # `from m import n` loads submodule m/n when there is one, else m itself;
        # roots are tried in turn, as Python walks sys.path. A package beats a
        # module of the same name, as it does for Python.
        return [
            [path for head in heads for path in (posixpath.join(head, name, "__init__.py"), posixpath.join(head, f"{name}.py"), *own(head))]
            for name in imp.names
        ]

    def _js(self, importer: str, imp: Import) -> list[list[str]]:
        spec = imp.module.split("?")[0].split("#")[0]
        if not (spec in (".", "..") or spec.startswith(("./", "../"))):
            return []  # a package, an alias, or an absolute path
        path = posixpath.normpath(posixpath.join(posixpath.dirname(importer), spec))
        if path == ".." or path.startswith("../"):
            return []  # outside the repo
        path = "" if path == "." else path
        stem, ext = posixpath.splitext(path)
        group = [path] if path else []
        group += [path + e for e in JS_EXTENSIONS]
        group += [stem + e for e in _TS_FOR_JS.get(ext, ())]
        group += [posixpath.join(path, "index" + e) for e in JS_EXTENSIONS]
        return [group]


def _python_imports(text: str) -> list[Import]:
    """Finds the word `import` and reads the statement from the start of its
    line, which is many times quicker than a line-anchored regex tried at
    every character. A line after an odd number of triple quotes is inside a
    triple-quoted string and skipped."""
    found: list[Import] = []
    line, counted, previous, doubles, singles = 1, 0, -1, 0, 0
    for hit in _PY_KEYWORD.finditer(text):
        start = text.rfind("\n", 0, hit.start()) + 1
        if start == previous:
            continue
        previous = start
        line += text.count("\n", counted, start)
        doubles += text.count('"""', counted, start)
        singles += text.count("'''", counted, start)
        counted = start
        if doubles % 2 or singles % 2:
            continue
        if match := _PY_IMPORT.match(text, start):
            for part in match.group(1).split(","):
                name = part.split()[0] if part.split() else ""
                if _PY_NAME.fullmatch(name):
                    found.append(Import(line, name.strip(".")))
        elif match := _PY_FROM.match(text, start):
            dots, module, names = match.groups()
            if dots or module:
                names = re.sub(r"#[^\n]*", "", names).strip().strip("()")
                listed = tuple(n.split()[0] for n in names.split(",") if n.split() and n.split()[0].isidentifier())
                found.append(Import(line, module.strip("."), listed, len(dots)))
    return found


def _js_imports(text: str) -> list[Import]:
    strings: list[tuple[int, int]] = []

    def blank_comment(match: re.Match) -> str:
        if match.group()[0] in "'\"`":
            strings.append(match.span())
            return match.group()
        return re.sub(r"[^\n]", " ", match.group())  # same length, so offsets still match `strings`

    text = _JS_COMMENT_OR_STRING.sub(blank_comment, text)
    starts = [start for start, _ in strings]

    def in_string(offset: int) -> bool:  # an import written inside a string, like a test fixture
        i = bisect.bisect_right(starts, offset) - 1
        return i >= 0 and offset < strings[i][1]

    found: list[Import] = []
    line, counted = 1, 0
    for match in _JS_IMPORT.finditer(text):
        before = text[match.start() - 1] if match.start() else " "
        if before.isalnum() or before in "_$." or in_string(match.start()):
            continue  # `obj.require(…)`, `reimport(…)`, or text in a string
        group = next(g for g in (2, 4, 6) if match.group(g))
        line += text.count("\n", counted, match.start(group))
        counted = match.start(group)
        found.append(Import(line, match.group(group)))
    return found


def _minified(text: str) -> bool:
    """A bundle's imports aren't anyone's design, and its long lines are slow to scan."""
    return len(text) > 4096 and len(text) > 300 * (text.count("\n") + 1)


def _read(repo_dir: str, path: str) -> str | None:
    full = os.path.join(repo_dir, *path.split("/"))
    try:
        if os.path.islink(full) or os.path.getsize(full) > MAX_BYTES:
            return None
        with open(full, encoding="utf-8", errors="replace") as handle:
            return handle.read()
    except OSError:
        return None
