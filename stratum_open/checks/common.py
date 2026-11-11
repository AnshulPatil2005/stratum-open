"""Small helpers the checks share: which files are tests, which lines are
comments, the new side of a file as far as the run can see it, and which
lines a check judges."""

from __future__ import annotations

import re
from collections.abc import Callable, Set

from stratum_open.diff import FileDiff

# What a check does on a file at a path, as names (its rules there): a path
# where the set is bigger is one where the check does more.
Scope = Callable[[str], Set[str]]

_TEST_DIR = re.compile(r"(?:^|/)(?:tests?|__tests__|__mocks__|spec|specs|testdata|fixtures?)/")
_TEST_FILE = re.compile(r"(?:^|/)(?:test_[^/]*\.py|[^/]*_test\.(?:py|go)|[^/]*\.(?:test|spec)\.[^/]+|conftest\.py)$")
_COMMENT = re.compile(r"^\s*(?:#|//|/\*|\*|--|<!--)")


def is_test_path(path: str) -> bool:
    return bool(_TEST_DIR.search(path) or _TEST_FILE.search(path))


def is_comment(text: str) -> bool:
    return bool(_COMMENT.match(text))


def extension(path: str) -> str:
    name = path.rsplit("/", 1)[-1]
    return name[name.rfind("."):].lower() if "." in name else ""


def basename(path: str) -> str:
    return path.rsplit("/", 1)[-1]


def view(file: FileDiff, contents: dict[str, str] | None) -> list[tuple[int, str]]:
    """The file's new side in line order: all of it when the caller passed its
    content, otherwise only the lines the diff shows, with gaps between hunks.
    Checks that track structure (a YAML block, a TOML table) restart at a gap,
    because they can't see what's in it."""
    text = (contents or {}).get(file.path)
    if text is not None:
        # Split on "\n" only, as git does: str.splitlines() also breaks on
        # characters like U+2028 and would shift the line numbers.
        return [(number, line.rstrip("\r")) for number, line in enumerate(text.split("\n"), start=1)]
    return sorted(file.new_side().items())


def moved_in(file: FileDiff, scope: Scope) -> bool:
    """Whether a rename brought the file from a path where the check does less
    than where it is now: a workflow moved in from `docs/`, code moved out of
    `tests/`. Its lines are new where they now are, though the diff shows
    none of them as added."""
    if file.status == "deleted" or file.binary or not file.old_path or file.old_path == file.path:
        return False
    return not scope(file.path) <= scope(file.old_path)


def checked_lines(file: FileDiff, contents: dict[str, str] | None, scope: Scope) -> list[tuple[int, str]]:
    """The lines a check judges: the ones the PR adds, or every line of a file
    that moved into the check's scope, when its text is at hand. A move that
    stays in scope keeps to the added lines, since the rest were checked
    where they were before."""
    if moved_in(file, scope) and (contents or {}).get(file.path) is not None:
        return view(file, contents)
    return file.added()
