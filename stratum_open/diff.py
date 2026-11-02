"""Unified diffs: which lines a PR adds, and their numbers in the new file.

Everything that comments on a line (the model's findings, the checks, GitHub's
inline comments) works in new-file line numbers, so this is the one place that
counts them.
"""

from __future__ import annotations

import codecs
import math
import re
from dataclasses import dataclass, field
from typing import Callable, Iterable

_HUNK = re.compile(r"^@@ -(\d+)(?:,(\d+))? \+(\d+)(?:,(\d+))? @@ ?(.*)$")


@dataclass
class Line:
    kind: str  # "+" added, "-" removed, " " unchanged
    text: str
    old: int | None
    new: int | None


@dataclass
class Hunk:
    old_start: int
    new_start: int
    header: str
    lines: list[Line] = field(default_factory=list)


@dataclass
class FileDiff:
    path: str  # the new path; the old one for a deleted file
    old_path: str | None = None
    status: str = "modified"  # added | deleted | modified | renamed
    binary: bool = False
    hunks: list[Hunk] = field(default_factory=list)

    @property
    def additions(self) -> int:
        return sum(1 for hunk in self.hunks for line in hunk.lines if line.kind == "+")

    @property
    def deletions(self) -> int:
        return sum(1 for hunk in self.hunks for line in hunk.lines if line.kind == "-")

    def added(self) -> list[tuple[int, str]]:
        """(new line number, text) of every added line."""
        return [(line.new, line.text) for hunk in self.hunks for line in hunk.lines if line.kind == "+" and line.new]

    def removed(self) -> list[str]:
        return [line.text for hunk in self.hunks for line in hunk.lines if line.kind == "-"]

    def new_side(self) -> dict[int, str]:
        """Every new-file line the diff shows, added or unchanged."""
        return {line.new: line.text for hunk in self.hunks for line in hunk.lines if line.new is not None}

    def can_comment(self, start: int, end: int | None = None) -> bool:
        """GitHub only takes a comment on new-file lines inside one hunk."""
        end = end or start
        if start > end:
            return False
        for hunk in self.hunks:
            shown = {line.new for line in hunk.lines if line.new is not None}
            if start in shown and end in shown:
                return all(number in shown for number in range(start, end + 1))
        return False

    def snippet(self, start: int, end: int | None = None) -> str:
        side = self.new_side()
        return "\n".join(side[n] for n in range(start, (end or start) + 1) if n in side)


def parse_diff(text: str) -> list[FileDiff]:
    """Parse `git diff` / GitHub's `.diff` output. Unknown lines are skipped,
    so a truncated diff still yields the files before the cut."""
    files: list[FileDiff] = []
    current: FileDiff | None = None
    hunk: Hunk | None = None
    old_no = new_no = 0
    # Only \n ends a line in a diff. str.splitlines() also splits on
    # U+2028, U+0085 and friends, which would shift every later line number,
    # and let a crafted line look like an added one.
    for raw in text.split("\n"):
        raw = raw.removesuffix("\r")
        if raw.startswith("diff --git "):
            current = FileDiff(path=_path_from_git_header(raw))
            files.append(current)
            hunk = None
            continue
        if current is None:
            continue
        match = _HUNK.match(raw) if raw.startswith("@@") else None
        if match:
            old_no, new_no = int(match.group(1)), int(match.group(3))
            hunk = Hunk(old_no, new_no, match.group(5).strip())
            current.hunks.append(hunk)
            continue
        if hunk is None:
            _header_line(current, raw)
            continue
        if raw.startswith("+"):
            hunk.lines.append(Line("+", raw[1:], None, new_no))
            new_no += 1
        elif raw.startswith("-"):
            hunk.lines.append(Line("-", raw[1:], old_no, None))
            old_no += 1
        elif raw.startswith(" ") or raw == "":
            hunk.lines.append(Line(" ", raw[1:], old_no, new_no))
            old_no += 1
            new_no += 1
        # "\ No newline at end of file" and anything else: not a line of code
    return files


def _header_line(current: FileDiff, raw: str) -> None:
    if raw.startswith("new file mode"):
        current.status = "added"
    elif raw.startswith("deleted file mode"):
        current.status = "deleted"
    elif raw.startswith("rename from "):
        current.old_path = _unquote(raw[len("rename from "):])
        current.status = "renamed"
    elif raw.startswith("rename to "):
        current.path = _unquote(raw[len("rename to "):])
        current.status = "renamed"
    elif raw.startswith("Binary files ") or raw.startswith("GIT binary patch"):
        current.binary = True
    elif raw.startswith("--- "):
        old = _strip_prefix(_unquote(raw[4:].split("\t")[0]), "a/")
        if old != "/dev/null" and current.old_path is None:
            current.old_path = old
    elif raw.startswith("+++ "):
        new = _strip_prefix(_unquote(raw[4:].split("\t")[0]), "b/")
        if new == "/dev/null":
            current.status = "deleted"
            current.path = current.old_path or current.path
        else:
            current.path = new
    if current.old_path == current.path and current.status != "renamed":
        current.old_path = None


def _path_from_git_header(raw: str) -> str:
    rest = raw[len("diff --git "):]
    if rest.startswith('"'):
        end = rest.find('" ', 1)
        return _strip_prefix(_unquote(rest[: end + 1]), "a/") if end > 0 else rest
    # "a/x b/x": the two halves are the same length when the path didn't change.
    half = (len(rest) - 1) // 2
    if rest[half] == " " and rest[:half][2:] == rest[half + 1:][2:]:
        return rest[half + 1:][2:]
    return _strip_prefix(rest.rsplit(" b/", 1)[-1], "b/") if " b/" in rest else rest


def _strip_prefix(path: str, prefix: str) -> str:
    return path[len(prefix):] if path.startswith(prefix) else path


def _unquote(path: str) -> str:
    """git quotes paths with unusual characters as C strings."""
    path = path.strip()
    if len(path) >= 2 and path[0] == path[-1] == '"':
        raw = codecs.escape_decode(path[1:-1].encode("utf-8"))[0]
        return raw.decode("utf-8", errors="replace")
    return path


# ── what the model sees ──────────────────────────────────────────────────


def render(file: FileDiff, hunks: Iterable[Hunk] | None = None) -> str:
    """A file's diff with each new-side line numbered, so the model can say
    which line a problem is on:

        ## path/to/file.py (modified)
        @@ def handler
           41    unchanged
           42 +  added
              -  removed
    """
    lines = [f"## {file.path} ({file.status})"]
    for hunk in hunks if hunks is not None else file.hunks:
        lines.append(f"@@ {hunk.header}".rstrip())
        for line in hunk.lines:
            number = f"{line.new:>6}" if line.new is not None else " " * 6
            lines.append(f"{number} {line.kind}  {line.text}")
    return "\n".join(lines)


def approx_tokens(text: str) -> int:
    """Code runs about three characters a token; erring high keeps a small
    model's context from overflowing."""
    return math.ceil(len(text) / 3)


@dataclass
class Packed:
    chunks: list[str]  # rendered diffs, one per model call
    reviewed: list[str]  # paths sent in full
    clipped: list[str]  # paths sent in part
    omitted: list[str]  # paths that didn't fit
    skipped: list[str]  # paths not sent on purpose (binary, deleted, ignored)


def pack(
    files: list[FileDiff],
    budget_tokens: int,
    max_chunks: int,
    *,
    skip: Callable[[str], bool] = lambda path: False,
    priority: Callable[[FileDiff], float] | None = None,
    count: Callable[[str], int] = approx_tokens,
) -> Packed:
    """Fill up to `max_chunks` chunks of `budget_tokens`, most important files
    first. A file too big for a chunk on its own is clipped to the hunks that
    fit rather than dropped."""
    packed = Packed([], [], [], [], [])
    candidates = []
    for file in files:
        if file.binary or file.status == "deleted" or not file.hunks or skip(file.path):
            packed.skipped.append(file.path)
        else:
            candidates.append(file)
    order = priority or (lambda f: f.additions + f.deletions)
    candidates.sort(key=lambda f: -order(f))

    chunk: list[str] = []
    used = 0
    for file in candidates:
        text = render(file)
        size = count(text) + 1
        if size > budget_tokens:
            text = _clip(file, budget_tokens, count)
            size = count(text) + 1 if text else 0
            if not text:
                packed.omitted.append(file.path)
                continue
            clipped = True
        else:
            clipped = False
        if used + size > budget_tokens and chunk:
            packed.chunks.append("\n\n".join(chunk))
            chunk, used = [], 0
        if len(packed.chunks) >= max_chunks:
            packed.omitted.append(file.path)
            continue
        chunk.append(text)
        used += size
        (packed.clipped if clipped else packed.reviewed).append(file.path)
    if chunk and len(packed.chunks) < max_chunks:
        packed.chunks.append("\n\n".join(chunk))
    return packed


def _clip(file: FileDiff, budget_tokens: int, count: Callable[[str], int]) -> str:
    """The whole hunks that fit, then as many lines of the next one as fit:
    a new file is a single hunk, and it shouldn't vanish for being long."""
    kept: list[Hunk] = []
    for hunk in file.hunks:
        if count(render(file, kept + [hunk])) <= budget_tokens:
            kept.append(hunk)
            continue
        low, high = 0, len(hunk.lines)
        while low < high:
            middle = (low + high + 1) // 2
            part = Hunk(hunk.old_start, hunk.new_start, hunk.header, hunk.lines[:middle])
            if count(render(file, kept + [part])) <= budget_tokens:
                low = middle
            else:
                high = middle - 1
        if low:
            kept.append(Hunk(hunk.old_start, hunk.new_start, hunk.header, hunk.lines[:low]))
        break
    return render(file, kept) if kept else ""
