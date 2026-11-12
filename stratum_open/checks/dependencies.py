"""Dependencies a PR adds, and whether each one is the package it seems to be.

A dependency is new when its name is on an added line of a manifest, and on
no removed line of any manifest of the same ecosystem nor on another line of
the same file. So bumping a version, or moving a package between sections or
files, doesn't count. Packages from a local path or a workspace aren't
third-party and aren't reported; ones from git or a URL are, but get no
registry or look-alike checks. Go's `// indirect` requirements are left out:
they come with another module.

Manifests are read line by line, so a line's section is known only when the
diff (or the file's full content, from `contents`) shows the section's
header. Without the header a line counts only if it looks unmistakably like
a dependency:

- `package.json`: a `"name": "^1.2.3"`-style line (a version range, a tag
  like `latest`, or `npm:`/`git+`/`github:` and the like) whose key isn't a
  known top-level field. A `devDependencies` entry and a `resolutions` entry
  look the same, so the second can be taken for a dependency.
- `pyproject.toml`: a quoted requirement with a version, extras or marker,
  such as `"httpx>=0.27"`. A bare `"httpx"` isn't told apart from a keyword.
  Poetry tables are read only when their header is shown.
- `Cargo.toml`: `name = "1.2"` or `name = { version = ... }`.
- `go.mod`: a module path and version.

The only thing sent to a registry is a dependency's name.
"""

from __future__ import annotations

import re
from dataclasses import dataclass
from datetime import datetime, timezone
from typing import Any
from urllib.parse import quote

import requests

from stratum_open import __version__
from stratum_open.checks.common import basename, checked_lines, view
from stratum_open.checks.popular import BY_ECOSYSTEM
from stratum_open.diff import FileDiff
from stratum_open.findings import Finding
from stratum_open.policy import Policy

REGISTRY_NAMES = {"pypi": "PyPI", "npm": "npm", "crates": "crates.io", "go": "the Go module proxy"}
LOOKUP_ECOSYSTEMS = ("pypi", "npm", "crates")  # Go modules have no first-published date to ask for
MAX_LOOKUPS = 30  # registry calls per run; a PR adding more gets the rest unchecked
USER_AGENT = f"stratum-open/{__version__} (dependency check)"


@dataclass
class Dependency:
    name: str  # as written in the manifest
    ecosystem: str  # pypi | npm | crates | go
    path: str
    line: int
    from_registry: bool = True  # False for git or URL sources

    @property
    def key(self) -> str:
        return normalise(self.name, self.ecosystem)


def normalise(name: str, ecosystem: str) -> str:
    """The form each registry compares names in: PEP 503 for PyPI, and
    crates.io treats `-` and `_` as the same."""
    if ecosystem == "pypi":
        return re.sub(r"[-_.]+", "-", name).lower()
    if ecosystem == "crates":
        return name.lower().replace("_", "-")
    if ecosystem == "npm":
        return name.lower()
    return name


def check(
    files: list[FileDiff],
    policy: Policy,
    *,
    lookup: Any = None,
    contents: dict[str, str] | None = None,
    now: datetime | None = None,
) -> list[Finding]:
    """`lookup` is a `PackageLookup` (or anything with its `info` method);
    None means no registry calls."""
    now = now or datetime.now(timezone.utc)
    findings: list[Finding] = []
    asked: set[tuple[str, str]] = set()
    for dep in find_new(files, contents):
        findings.append(_new(dep, policy))
        if not dep.from_registry:
            continue
        similar = lookalike(dep.name, dep.ecosystem)
        if similar:
            findings.append(_lookalike(dep, similar))
        if lookup is None or dep.ecosystem not in LOOKUP_ECOSYSTEMS:
            continue
        if (dep.ecosystem, dep.key) not in asked and len(asked) >= MAX_LOOKUPS:
            continue
        asked.add((dep.ecosystem, dep.key))
        try:
            info = lookup.info(dep.ecosystem, dep.name)
        except Exception:  # a lookup problem means "unknown", never a failed review
            info = None
        if info is None:
            continue
        if not info.exists:
            findings.append(_missing(dep))
        elif info.first_published is not None:
            days = (now - info.first_published).days
            if days < policy.dependencies.min_package_age_days:
                findings.append(_young(dep, days))
    return findings


def _finding(dep: Dependency, rule: str, title: str, detail: str, suggestion: str, severity: str,
             needs_human: bool, confidence: float) -> Finding:
    return Finding(
        title=title, detail=detail, severity=severity, category="supply-chain", path=dep.path,
        line_start=dep.line, line_end=dep.line, suggestion=suggestion, source="check", rule=rule,
        confidence=confidence, needs_human=needs_human,
    )


def _new(dep: Dependency, policy: Policy) -> Finding:
    where = REGISTRY_NAMES[dep.ecosystem] if dep.from_registry else "a git repository or URL"
    detail = (
        f"This PR adds `{dep.name}` (from {where}) as a dependency. Every new package is someone else's "
        "code running in this project, and its own dependencies come with it."
    )
    if not dep.from_registry:
        detail += " Code from git or a URL isn't checked by any registry and can change without a new version."
    return _finding(
        dep, "deps/new", f"New dependency: `{dep.name}`", detail,
        "Check it's needed, maintained, and published by someone you trust.",
        "low", policy.always_human.new_dependencies, 1.0,
    )


def _lookalike(dep: Dependency, similar: str) -> Finding:
    registry = REGISTRY_NAMES[dep.ecosystem]
    return _finding(
        dep, "deps/lookalike", f"`{dep.name}` looks like `{similar}`",
        (
            f"`{dep.name}` is a near-copy of the name `{similar}`, one of the most-used packages on {registry}, "
            "but it's a different package. Names one typo away from a popular one are a common way to get "
            "malware installed (typosquatting)."
        ),
        f"If you meant `{similar}`, fix the name. If `{dep.name}` is intended, a maintainer should confirm it's the right package.",
        "high", True, 0.6,
    )


def _missing(dep: Dependency) -> Finding:
    registry = REGISTRY_NAMES[dep.ecosystem]
    return _finding(
        dep, "deps/missing", f"`{dep.name}` isn't on {registry}",
        (
            f"No package called `{dep.name}` exists on {registry}. It may be a typo, or a private package that "
            "needs its own index. It may also be a name an AI assistant made up, which anyone could register "
            'with malicious code for the next person who installs it ("slopsquatting").'
        ),
        "Check the name. If it's private, make sure installs can only ever come from your own index.",
        "high", True, 0.9,
    )


def _young(dep: Dependency, days: int) -> Finding:
    registry = REGISTRY_NAMES[dep.ecosystem]
    age = "today" if days <= 0 else "1 day ago" if days == 1 else f"{days} days ago"
    return _finding(
        dep, "deps/young", f"`{dep.name}` is a brand-new package",
        (
            f"`{dep.name}` was first published to {registry} {age}. Most malicious packages are caught and "
            "removed within their first weeks, so a new one hasn't had that scrutiny yet."
        ),
        "Check who publishes it and that it's the package you meant, or wait until it has some history.",
        "high", True, 0.9,
    )


# ── finding the new ones ─────────────────────────────────────────────────

_REQUIREMENTS = re.compile(r"(?:[\w.-]*[-_.])?requirements(?:[-_.][\w.-]*)?\.(?:txt|in)")
_IN_REQUIREMENTS_DIR = re.compile(r"(?:^|/)requirements/[^/]+\.(?:txt|in)$")
_ECOSYSTEM = {"requirements": "pypi", "pyproject": "pypi", "npm": "npm", "cargo": "crates", "go": "go"}


def manifest_kind(path: str) -> str | None:
    name = basename(path).lower()
    if name == "package.json":
        return "npm"
    if name == "pyproject.toml":
        return "pyproject"
    if name == "cargo.toml":
        return "cargo"
    if name == "go.mod":
        return "go"
    if _REQUIREMENTS.fullmatch(name) or _IN_REQUIREMENTS_DIR.search(path.lower()):
        return "requirements"
    return None


def scope(path: str) -> frozenset[str]:
    """The kind of manifest at `path`, if it's one."""
    kind = manifest_kind(path)
    return frozenset({kind}) if kind else frozenset()


def find_new(files: list[FileDiff], contents: dict[str, str] | None = None) -> list[Dependency]:
    """A file renamed to a manifest from a path that wasn't one of its kind
    (`examples/deps.txt` to `requirements.txt`) counts every line as added."""
    manifests = [(file, kind) for file in files if (kind := manifest_kind(file.path)) and not file.binary]
    removed: dict[str, set[str]] = {}
    for file, kind in manifests:
        ecosystem = _ECOSYSTEM[kind]
        removed.setdefault(ecosystem, set()).update(
            normalise(name, ecosystem) for text in file.removed() for name in _loose_names(kind, text)
        )
    found: list[Dependency] = []
    for file, kind in manifests:
        ecosystem = _ECOSYSTEM[kind]
        added = {number for number, _ in checked_lines(file, contents, scope)}
        entries = _SCANNERS[kind](view(file, contents))
        elsewhere = {normalise(name, ecosystem) for number, name, _ in entries if number not in added}
        seen: set[str] = set()
        for number, name, source in entries:
            key = normalise(name, ecosystem)
            if number not in added or source == "local" or key in seen:
                continue
            seen.add(key)
            if key in removed[ecosystem] or key in elsewhere:
                continue
            found.append(Dependency(name, ecosystem, file.path, number, from_registry=source == "registry"))
    return found


# Each scanner returns (line, name, source) for every dependency line it can
# see, added or not. source: "registry", "remote" (git or URL) or "local".
Entry = tuple[int, str, str]
# One alternative per quote, with the backslash kept out of the plain-character class, so a
# backslash is consumed one way only: the ambiguous form backtracks exponentially on long lines.
_STRING = re.compile(r"""\"((?:\\.|[^"\\])*)\"|'((?:\\.|[^'\\])*)'""")
_NAME = re.compile(r"^([A-Za-z0-9](?:[A-Za-z0-9._-]*[A-Za-z0-9])?)\s*(\[[^\]]*\])?\s*(.*)$", re.S)


def _quoted(match: re.Match) -> str:
    """The body of a `_STRING` match, whichever quote it used."""
    body = match.group(1)
    return body if body is not None else match.group(2)


def _requirement(text: str, strict: bool = False) -> tuple[str, str] | None:
    """(name, source) from a PEP 508 requirement. `strict` asks for a version,
    extras or marker as well, to tell a requirement from any other string."""
    text = re.sub(r"(?:^|\s)#.*$", "", text.lstrip("\ufeff")).strip()
    if not text or text.startswith(("-", ".", "/", "~")) or re.match(r"^[\w+.-]+://", text):
        return None
    match = _NAME.match(text)
    if not match:
        return None
    rest = match.group(3).strip()
    if rest and not re.match(r"^(?:[<>=!~;@,(]|\\$)", rest):
        return None
    if strict and not (rest or match.group(2)):
        return None
    return match.group(1), "remote" if rest.startswith("@") else "registry"


def _scan_requirements(lines: list[tuple[int, str]]) -> list[Entry]:
    entries = []
    for number, text in lines:
        requirement = _requirement(text)
        if requirement:
            entries.append((number, *requirement))
    return entries


# TOML, read a line at a time.
_TABLE = re.compile(r"^\s*\[\[?\s*([A-Za-z0-9_-].*?)\s*\]\]?\s*(?:#.*)?$")
_TOML_KEY = re.compile(r"""^\s*(?P<key>[A-Za-z0-9_.-]+|"[^"]*"|'[^']*')\s*=\s*(?P<value>.*)$""")
_HIDDEN = "?"  # a section or array whose start is in a gap the diff doesn't show


def _bare(text: str) -> str:
    """A line with its strings emptied and its comment cut, to count brackets."""
    return re.sub(r"\s*#.*$", "", _STRING.sub('""', text))


def _toml_depth(text: str) -> int:
    bare = _bare(text)
    return bare.count("[") + bare.count("{") - bare.count("]") - bare.count("}")


def _toml_lines(lines: list[tuple[int, str]]):
    """Walk TOML lines as (number, text, table, array_key, key, value):
    `table` is None when a gap hides the header, `array_key` names the
    multi-line array the line sits in (`_HIDDEN` when that's hidden too),
    and key/value are set on `key = value` lines."""
    table: str | None = ""
    array: str | None = None
    depth = 0
    previous: int | None = None
    for number, text in lines:
        if number != (previous or 0) + 1:
            table, array, depth = None, _HIDDEN, 0
        previous = number
        if array == _HIDDEN and (_TABLE.match(text) or _TOML_KEY.match(text)):
            array = None  # keys and headers can't be inside an array
        if array is not None:
            yield number, text, table, array, None, None
            depth += _toml_depth(text)
            if (array == _HIDDEN and "]" in _bare(text)) or (array != _HIDDEN and depth <= 0):
                array, depth = None, 0
            continue
        header = _TABLE.match(text)
        if header:
            table = re.sub(r"\s*\.\s*", ".", header.group(1)).replace('"', "").replace("'", "")
            yield number, text, table, None, None, None
            continue
        key_line = _TOML_KEY.match(text)
        if key_line:
            key = key_line.group("key").strip("\"'")
            value = key_line.group("value")
            yield number, text, table, None, key, value
            if value.lstrip().startswith("["):
                depth = _toml_depth(value)
                if depth > 0:
                    array = key
            continue
        yield number, text, table, None, None, None


def _pyproject_deps_array(table: str | None, key: str | None) -> bool:
    if table is None:
        return key in ("dependencies", "requires", "dev-dependencies")
    return (
        (table == "project" and key == "dependencies")
        or table in ("project.optional-dependencies", "dependency-groups", "tool.pdm.dev-dependencies")
        or (table == "build-system" and key == "requires")
        or (table == "tool.uv" and key == "dev-dependencies")
    )


_POETRY_TABLE = re.compile(r"tool\.poetry\.(?:dependencies|dev-dependencies|group\.[^.]+\.dependencies)")


def _scan_pyproject(lines: list[tuple[int, str]]) -> list[Entry]:
    entries = []
    for number, text, table, array, key, value in _toml_lines(lines):
        if array is not None:
            # Inside a multi-line array: its items, if it's one of dependencies.
            # When even the array's key is hidden, only clear requirements count.
            hidden = array == _HIDDEN
            if hidden or _pyproject_deps_array(table, array):
                entries.extend(_strings_as_requirements(number, text, strict=hidden))
        elif key is not None and value.lstrip().startswith("["):
            if _pyproject_deps_array(table, key):
                entries.extend(_strings_as_requirements(number, value, strict=False))
        elif key is not None and table and _POETRY_TABLE.fullmatch(table) and key.lower() != "python":
            entries.append((number, key, _inline_source(value)))
    return entries


def _strings_as_requirements(number: int, text: str, strict: bool) -> list[Entry]:
    found = []
    for match in _STRING.finditer(text):
        if text[: match.start()].rstrip().endswith("="):
            continue  # a value in an inline table, like {include-group = "test"}
        requirement = _requirement(_quoted(match), strict=strict)
        if requirement:
            found.append((number, *requirement))
    return found


def _inline_source(value: str) -> str:
    """Where a Poetry or Cargo entry comes from, by the keys of its inline table."""
    if re.search(r"\b(?:path|workspace)\s*=", value):
        return "local"
    if re.search(r"\b(?:git|url)\s*=", value):
        return "remote"
    return "registry"


_CARGO_TABLE = re.compile(r"(?:target\..+\.|workspace\.)?(?:dev-|build-)?dependencies")
_CARGO_SUBTABLE = re.compile(r"(?:target\..+\.|workspace\.)?(?:dev-|build-)?dependencies\.([A-Za-z0-9_-]+)")
_CARGO_FIELDS = {
    "name", "version", "edition", "rust-version", "authors", "description", "license", "license-file",
    "repository", "homepage", "documentation", "readme", "keywords", "categories", "publish", "build",
    "links", "exclude", "include", "resolver", "members", "default-members", "workspace", "autobins",
    "opt-level", "debug", "lto", "codegen-units", "panic", "strip", "incremental", "overflow-checks",
}
_CARGO_VERSION = re.compile(r"""^\s*(?P<q>["'])(?:[\^~=]|[<>]=?)?\s*(?:\d+(?:\.\d+){0,2}(?:-[\w.]+)?|\*)(?:\s*,.*)?(?P=q)""")


def _scan_cargo(lines: list[tuple[int, str]]) -> list[Entry]:
    entries: list[Entry] = []
    subtable: int | None = None  # index in entries of the `[dependencies.name]` being read
    for number, text, table, array, key, value in _toml_lines(lines):
        if array is not None:
            continue
        if key is None:
            sub = _CARGO_SUBTABLE.fullmatch(table or "") if _TABLE.match(text) else None
            if sub:
                entries.append((number, sub.group(1), "registry"))
                subtable = len(entries) - 1
            elif _TABLE.match(text):
                subtable = None
            continue
        if subtable is not None and table and _CARGO_SUBTABLE.fullmatch(table):
            line, name, source = entries[subtable]
            if key == "package":
                quoted = _STRING.search(value)
                name = _quoted(quoted) if quoted else name
            elif key in ("path", "workspace"):
                source = "local"
            elif key == "git" and source != "local":
                source = "remote"
            entries[subtable] = (line, name, source)
            continue
        if table is not None and not _CARGO_TABLE.fullmatch(table):
            continue
        if table is None and (key in _CARGO_FIELDS or not (_CARGO_VERSION.match(value) or re.search(r"\b(?:version|git)\s*=", value))):
            continue
        name, _, field = key.partition(".")  # a dotted key: serde.workspace = true
        if value.lstrip().startswith("{"):
            package = re.search(r"""\bpackage\s*=\s*["']([^"']+)["']""", value)
            name, source = (package.group(1) if package else name), _inline_source(value)
        else:
            source = {"path": "local", "workspace": "local", "git": "remote"}.get(field, "registry")
        entries.append((number, name, source))
    return entries


# package.json
_JSON_KEY = re.compile(r'^\s*"((?:\\.|[^"\\])*)"\s*:\s*(.*)$')
_JSON_STRING = re.compile(r'"(?:\\.|[^"\\])*"')
_NPM_SECTIONS = {"dependencies", "devDependencies", "peerDependencies", "optionalDependencies"}
_NPM_NAME = re.compile(r"(?:@[a-z0-9][\w.-]*/)?[a-z0-9][\w.-]*")
_NPM_SPEC = re.compile(
    r"^(?:(?:[\^~]|[<>]=?|=)?\s*v?\d+(?:\.(?:\d+|[xX*]))*(?:[-+][\w.-]+)?(?:\s.*)?|[*xX]|latest|next|beta|alpha|canary|rc"
    r"|(?:workspace|npm|file|link|portal|patch|catalog|git|git\+[a-z]+|github|gitlab|bitbucket):.*)$"
)
_NPM_FIELDS = {
    "name", "version", "description", "main", "module", "browser", "types", "typings", "type", "license",
    "author", "homepage", "private", "node", "npm", "yarn", "pnpm", "bun", "deno", "vscode", "tag", "access",
    "registry",
}


def _scan_package_json(lines: list[tuple[int, str]]) -> list[Entry]:
    entries = []
    stack: list[str] = []  # the key of each open object; "" for the root
    previous: int | None = None
    for number, text in lines:
        if number != (previous or 0) + 1:
            stack = [_HIDDEN]  # a gap: where this line sits is hidden
        previous = number
        key_line = _JSON_KEY.match(text)
        if key_line and stack:
            key, value = key_line.group(1), key_line.group(2).strip().rstrip(",").strip()
            spec = value[1:-1] if len(value) >= 2 and value[0] == value[-1] == '"' else None
            section = stack[-1]
            if spec is not None and (
                (section in _NPM_SECTIONS and len(stack) == 2)
                or (section == _HIDDEN and key not in _NPM_FIELDS and _NPM_NAME.fullmatch(key) and _NPM_SPEC.match(spec))
            ):
                entries.append((number, *_npm_entry(key, spec)))
        opened = key_line.group(1) if key_line else ""
        for char in _JSON_STRING.sub('""', text):
            if char in "{[":
                stack.append(opened)
                opened = ""
            elif char in "}]" and stack:
                stack.pop()
                if not stack:
                    stack = [_HIDDEN]  # closed past what's shown
    return entries


def _npm_entry(key: str, spec: str) -> tuple[str, str]:
    if spec.startswith("npm:"):  # an alias: "lodash4": "npm:lodash@^4"
        target = spec[4:]
        return (target.rsplit("@", 1)[0] if "@" in target.lstrip("@") else target), "registry"
    if spec.startswith(("workspace:", "file:", "link:", "portal:", "catalog:")):
        return key, "local"
    if re.match(r"^(?:git|git\+[a-z]+|github|gitlab|bitbucket|https?):|^[\w.-]+/[\w.-]+(?:#.*)?$", spec):
        return key, "remote"
    return key, "registry"


# go.mod
_GO_BLOCK = re.compile(r"^\s*(?P<keyword>require|replace|exclude|retract|tool|godebug)\s*\(\s*(?://.*)?$")
_GO_REQUIRE = re.compile(r"^\s*require\s+(?P<path>\S+)\s+(?P<version>v\S+)(?P<rest>.*)$")
_GO_ENTRY = re.compile(r"^\s*(?P<path>[^\s/()]+\.[^\s/()]+(?:/\S*)?)\s+(?P<version>v\d\S*)(?P<rest>.*)$")


def _scan_go_mod(lines: list[tuple[int, str]]) -> list[Entry]:
    entries = []
    block: str | None = None
    previous: int | None = None
    for number, text in lines:
        if number != (previous or 0) + 1:
            block = _HIDDEN
        previous = number
        if text.strip() == ")":
            block = None
            continue
        opened = _GO_BLOCK.match(text)
        if opened:
            block = opened.group("keyword")
            continue
        match = _GO_REQUIRE.match(text) if block in (None, _HIDDEN) else None
        if match is None and block in ("require", _HIDDEN):
            match = _GO_ENTRY.match(text)
        if match and "=>" not in match.group("rest") and "// indirect" not in match.group("rest"):
            entries.append((number, match.group("path"), "registry"))
    return entries


_SCANNERS = {
    "requirements": _scan_requirements,
    "pyproject": _scan_pyproject,
    "npm": _scan_package_json,
    "cargo": _scan_cargo,
    "go": _scan_go_mod,
}


def _loose_names(kind: str, text: str) -> list[str]:
    """Every name a removed line could have been a dependency entry for. The
    old side's sections aren't known, so this errs towards finding a name:
    a name it finds wrongly only keeps an added line from counting as new."""
    if kind == "requirements":
        requirement = _requirement(text)
        return [requirement[0]] if requirement else []
    if kind == "npm":
        key = _JSON_KEY.match(text)
        if not key:
            return []
        alias = re.search(r'"npm:(@?[^@"]+)', text)
        return [key.group(1)] + ([alias.group(1)] if alias else [])
    if kind == "go":
        match = _GO_REQUIRE.match(text) or _GO_ENTRY.match(text)
        return [match.group("path")] if match else []
    names = [requirement[0] for match in _STRING.finditer(text) if (requirement := _requirement(_quoted(match)))]
    key = _TOML_KEY.match(text)
    if key:
        names.append(key.group("key").strip("\"'"))
        package = re.search(r"""\bpackage\s*=\s*["']([^"']+)["']""", text)
        if package:
            names.append(package.group(1))
    header = _TABLE.match(text)
    if header:
        names.append(header.group(1).rsplit(".", 1)[-1])
    return names


# ── look-alike names ─────────────────────────────────────────────────────

_POPULAR = {
    ecosystem: {normalise(name, ecosystem): name for name in names} for ecosystem, names in BY_ECOSYSTEM.items()
}
_SEPARATORS = re.compile(r"[-_.]")
_PARTS = re.compile(r"[-_./]")


def lookalike(name: str, ecosystem: str) -> str | None:
    """The popular package `name` is probably a typo of, if any.

    Names are compared a part at a time (split on `-_./`), so plugin families
    like `pytest-env` and `pytest-cov` aren't taken for each other: only
    names that differ in one part count, and that part has to be within one
    edit of the popular one's (two if it's 8+ characters long), or have two
    neighbouring characters swapped. Parts under five characters count only
    for a swap. Names that differ only in `-_.` always count."""
    popular = _POPULAR.get(ecosystem)
    if not popular:
        return None
    key = normalise(name, ecosystem)
    if key in popular:
        return None
    squashed = _SEPARATORS.sub("", key)
    for candidate, shown in popular.items():
        if _SEPARATORS.sub("", candidate) == squashed:
            return shown
    parts = _PARTS.split(key)
    for candidate, shown in popular.items():
        theirs = _PARTS.split(candidate)
        if len(theirs) != len(parts):
            continue
        differing = [(mine, other) for mine, other in zip(parts, theirs, strict=True) if mine != other]
        if len(differing) == 1 and _close(*differing[0]):
            return shown
    return None


def _close(mine: str, theirs: str) -> bool:
    if min(len(mine), len(theirs)) < 3:
        return False
    if _swapped(mine, theirs):
        return True
    limit = 2 if len(theirs) >= 8 else 1 if len(theirs) >= 5 else 0
    return limit > 0 and abs(len(mine) - len(theirs)) <= limit and _distance(mine, theirs) <= limit


def _swapped(a: str, b: str) -> bool:
    if len(a) != len(b):
        return False
    differing = [i for i in range(len(a)) if a[i] != b[i]]
    return len(differing) == 2 and differing[1] == differing[0] + 1 and a[differing[0]] == b[differing[1]] and a[differing[1]] == b[differing[0]]


def _distance(a: str, b: str) -> int:
    """Edit distance, counting a swap of neighbours as one edit."""
    rows = [list(range(len(b) + 1))]
    for i in range(1, len(a) + 1):
        row = [i] + [0] * len(b)
        for j in range(1, len(b) + 1):
            row[j] = min(rows[-1][j] + 1, row[j - 1] + 1, rows[-1][j - 1] + (a[i - 1] != b[j - 1]))
            if i > 1 and j > 1 and a[i - 1] == b[j - 2] and a[i - 2] == b[j - 1]:
                row[j] = min(row[j], rows[-2][j - 2] + 1)
        rows.append(row)
    return rows[-1][-1]


# ── registries ───────────────────────────────────────────────────────────


@dataclass(frozen=True)
class PackageInfo:
    exists: bool
    first_published: datetime | None = None  # None when the registry doesn't say


class PackageLookup:
    """Asks PyPI, npm and crates.io whether a package exists and when it was
    first published. Only the name is sent. Answers are kept for the run, and
    anything that goes wrong (no network, a timeout, a rate limit, a reply it
    can't read) is "unknown": `info` returns None, never raises."""

    def __init__(self, session: Any = None, timeout: float = 10.0):
        self.session = session or requests.Session()
        self.timeout = timeout
        self._cache: dict[tuple[str, str], PackageInfo | None] = {}

    def info(self, ecosystem: str, name: str) -> PackageInfo | None:
        key = (ecosystem, normalise(name, ecosystem))
        if key not in self._cache:
            self._cache[key] = self._fetch(ecosystem, name)
        return self._cache[key]

    def _fetch(self, ecosystem: str, name: str) -> PackageInfo | None:
        if ecosystem == "pypi":
            url = f"https://pypi.org/pypi/{quote(normalise(name, 'pypi'), safe='')}/json"
        elif ecosystem == "npm":
            url = f"https://registry.npmjs.org/{quote(name, safe='@')}"  # @scope/name -> @scope%2Fname
        elif ecosystem == "crates":
            url = f"https://crates.io/api/v1/crates/{quote(name, safe='')}"
        else:
            return None
        try:
            response = self.session.get(
                url, timeout=self.timeout, headers={"User-Agent": USER_AGENT, "Accept": "application/json"}
            )
            if response.status_code == 404:
                return PackageInfo(exists=False)
            if response.status_code != 200:
                return None
            data = response.json()
        except (requests.RequestException, ValueError):
            return None
        if not isinstance(data, dict):
            return None
        return PackageInfo(exists=True, first_published=_first_published(ecosystem, data))


def _first_published(ecosystem: str, data: dict) -> datetime | None:
    if ecosystem == "pypi":
        releases = data.get("releases")
        times = [
            _time(upload.get("upload_time_iso_8601") or upload.get("upload_time"))
            for uploads in (releases.values() if isinstance(releases, dict) else ())
            for upload in (uploads if isinstance(uploads, list) else ())
            if isinstance(upload, dict)
        ]
        times = [t for t in times if t is not None]
        return min(times) if times else None
    if ecosystem == "npm":
        published = data.get("time")
        return _time(published.get("created")) if isinstance(published, dict) else None
    crate = data.get("crate")
    return _time(crate.get("created_at")) if isinstance(crate, dict) else None


def _time(value: Any) -> datetime | None:
    if not isinstance(value, str):
        return None
    try:
        parsed = datetime.fromisoformat(value.replace("Z", "+00:00"))
    except ValueError:
        return None
    return parsed if parsed.tzinfo else parsed.replace(tzinfo=timezone.utc)
