"""Hotspots: source files that change often and are hard to follow, where a
mistake is both likely and costly.

Churn is the number of commits on `ref` that touched a file in the window,
counted under the file's name at `ref`: when git sees a rename (`-M`, at least
half the content kept), the old name's commits carry over. A rename that
rewrote most of the file starts its count again.

Complexity is a rough, language-blind count of decision points in the file at
`ref`: `if elif for while case catch except and or`, `&&`, `||` and `?`, with
comments and string literals left out roughly (`#` comments for Python, Ruby,
shell and the like, `//` and `/* */` for the rest). It ranks files, it isn't a
metric to quote.

A file is a hotspot when it changed at least twice and is at least as complex
as the median source file changed in the window, so a busy but simple file
(a version number, a list of constants) isn't one. Hotspots rank by churn ×
complexity. Tests are left out: they change with the code they test and are
seldom where the risk is. So are minified files, files over 1 MB and vendored
folders; drop anything else the policy ignores before using the result.
"""

from __future__ import annotations

import posixpath
import re
import statistics
import subprocess
from collections import Counter
from dataclasses import dataclass
from datetime import datetime
from typing import Iterable

from stratum_open.repo.history import Commit, recent_commits

MAX_BYTES = 1_000_000
MIN_CHURN = 2
SOURCE_EXTENSIONS = frozenset({
    ".py", ".js", ".jsx", ".ts", ".tsx", ".mjs", ".cjs", ".mts", ".cts", ".vue", ".svelte",
    ".go", ".rs", ".java", ".kt", ".kts", ".scala", ".groovy", ".cs", ".fs", ".swift", ".dart",
    ".c", ".h", ".cc", ".cpp", ".cxx", ".hpp", ".hh", ".m", ".mm", ".zig",
    ".rb", ".php", ".pl", ".pm", ".sh", ".bash", ".lua", ".r", ".ex", ".exs", ".erl", ".jl", ".nim",
})
_HASH_COMMENTS = frozenset({".py", ".rb", ".pl", ".pm", ".sh", ".bash", ".r", ".ex", ".exs", ".jl", ".nim"})
_SKIP_FOLDERS = frozenset({"vendor", "third_party", "node_modules", "dist"})
_TEST_FOLDERS = frozenset({"test", "tests", "__tests__", "spec", "specs"})
_TEST_FILE = re.compile(r"^(?:test_.*|.*_test|.*\.(?:test|spec)|conftest|.*[a-z0-9]Tests?)\.\w+$")

_STRING = r""""(?:\\.|[^"\\\n])*"|'(?:\\.|[^'\\\n])*'"""
_HASH_NOISE = re.compile(r'"""[\s\S]*?"""|\'\'\'[\s\S]*?\'\'\'|' + _STRING + r"|#[^\n]*")
_SLASH_NOISE = re.compile(_STRING + r"|`(?:\\.|[^`\\])*`|//[^\n]*|/\*[\s\S]*?\*/")
_DECISION = re.compile(r"\b(?:if|elif|for|while|case|catch|except|and|or)\b|&&|\|\||\?\??")


@dataclass(frozen=True)
class Hotspot:
    path: str
    churn: int  # commits that touched it in the window
    complexity: int  # decision points in the file at `ref`
    score: int  # churn × complexity
    window_days: int = 180


def hotspots(
    repo_dir: str,
    ref: str = "HEAD",
    *,
    window_days: int = 180,
    top: int = 10,
    now: datetime | None = None,
) -> list[Hotspot]:
    """The `top` hotspots on `ref`, worst first. Short history means few or none."""
    churn = _churn(recent_commits(repo_dir, ref, window_days=window_days, now=now))
    texts = _read_at(repo_dir, ref, sorted(path for path in churn if is_source(path)))
    complexity = {path: decision_points(path, text) for path, text in texts.items()}
    if not complexity:
        return []
    median = statistics.median(complexity.values())
    found = [
        Hotspot(path, churn[path], points, churn[path] * points, window_days)
        for path, points in complexity.items()
        if churn[path] >= MIN_CHURN and points and points >= median
    ]
    found.sort(key=lambda h: (-h.score, -h.churn, h.path))
    return found[:top]


def hotspot_paths(hotspots: Iterable[Hotspot]) -> set[str]:
    return {hotspot.path for hotspot in hotspots}


def hotspot_note(hotspots: Iterable[Hotspot], changed_paths: Iterable[str]) -> list[str]:
    """A line for each changed file that is a hotspot, in the PR's order."""
    by_path = {hotspot.path: hotspot for hotspot in hotspots}
    notes = []
    for path in dict.fromkeys(changed_paths):
        hotspot = by_path.get(path)
        if hotspot is not None:
            notes.append(
                f"`{path}` is a hotspot: changed {hotspot.churn} times in the last {hotspot.window_days} days, and among the "
                f"more complex files ({hotspot.complexity} branches and conditions). Reviewed strictly."
            )
    return notes


def is_source(path: str) -> bool:
    """Code someone maintains by hand: not a test, minified or vendored file."""
    folders, name = path.split("/")[:-1], posixpath.basename(path)
    return (
        posixpath.splitext(name)[1].lower() in SOURCE_EXTENSIONS
        and ".min." not in name
        and not _TEST_FILE.match(name)
        and not any(folder in _TEST_FOLDERS or folder in _SKIP_FOLDERS for folder in folders)
    )


def decision_points(path: str, text: str) -> int:
    noise = _HASH_NOISE if posixpath.splitext(path)[1].lower() in _HASH_COMMENTS else _SLASH_NOISE
    return len(_DECISION.findall(noise.sub(" ", text)))


def _churn(commits: list[Commit]) -> Counter:
    """Commits per file, newest first, under each file's latest name."""
    counts: Counter = Counter()
    later_name: dict[str, str] = {}  # a name a file had then -> its name at `ref`
    for commit in commits:
        for change in commit.changes:
            current = later_name.get(change.path, change.path)
            counts[current] += 1
            if change.status == "R" and change.old_path:
                later_name[change.old_path] = current
    return counts


def _read_at(repo_dir: str, ref: str, paths: list[str]) -> dict[str, str]:
    """Each file's text at `ref`, in one git call. Files gone by then, and
    files over MAX_BYTES, are left out."""
    paths = [path for path in paths if "\n" not in path]
    if not paths:
        return {}
    request = "".join(f"{ref}:{path}\n" for path in paths).encode("utf-8")
    out = subprocess.run(
        ["git", "-C", repo_dir, "cat-file", "--batch"], input=request, capture_output=True, timeout=120, check=True,
    ).stdout
    texts, pos = {}, 0
    for path in paths:
        end = out.find(b"\n", pos)
        if end < 0:
            break
        header = out[pos:end].split(b" ")
        pos = end + 1
        if len(header) == 3 and header[2].isdigit():  # "<sha> <type> <size>", then the content
            size = int(header[2])
            if header[1] == b"blob" and size <= MAX_BYTES:
                texts[path] = out[pos:pos + size].decode("utf-8", errors="replace")
            pos += size + 1
    return texts
