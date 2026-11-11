"""Unified diffs: which lines a PR adds, and their numbers in the new file.

Everything that comments on a line (the model's findings, the checks, GitHub's
inline comments) works in new-file line numbers, so this is the one place that
counts them.
"""

from __future__ import annotations

import math
import re
from dataclasses import dataclass, field, replace
from typing import Callable, Iterable

from stratum_open import util  # not `count`, which `pack` takes as a parameter

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
        """GitHub only takes a comment on new-file lines inside one hunk, and
        none at all on a binary file or a submodule entry."""
        end = end or start
        if start > end or self.binary:
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
    old_no = new_no = old_left = new_left = 0
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
            # The header's counts say where the hunk ends (1 when left out).
            old_left, new_left = int(match.group(2) or 1), int(match.group(4) or 1)
            hunk = Hunk(old_no, new_no, match.group(5).strip())
            current.hunks.append(hunk)
            continue
        if hunk is None:
            _header_line(current, raw)
            continue
        if old_left <= 0 and new_left <= 0:
            continue  # past the hunk: the "" after the diff's last newline isn't a line of code
        if raw.startswith("+"):
            hunk.lines.append(Line("+", raw[1:], None, new_no))
            new_no += 1
            new_left -= 1
        elif raw.startswith("-"):
            hunk.lines.append(Line("-", raw[1:], old_no, None))
            old_no += 1
            old_left -= 1
        elif raw.startswith(" ") or raw == "":  # some tools drop the space of a blank unchanged line
            hunk.lines.append(Line(" ", raw[1:], old_no, new_no))
            old_no += 1
            new_no += 1
            old_left -= 1
            new_left -= 1
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
    elif raw.startswith("index ") and raw.rstrip().endswith(" 160000"):
        current.binary = True  # a submodule: the "Subproject commit" line is not code anyone can comment on
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
        return _unescape(path[1:-1].encode("utf-8")).decode("utf-8", errors="replace")
    return path


_C_ESCAPES = {b"a": b"\a", b"b": b"\b", b"f": b"\f", b"n": b"\n", b"r": b"\r", b"t": b"\t", b"v": b"\v", b"\\": b"\\", b'"': b'"'}


def _unescape(text: bytes) -> bytes:
    """git's C-style quoting undone: the named escapes and `\\NNN` octal
    bytes. An escape git wouldn't write is kept as it is. Done here rather
    than with `codecs.escape_decode`, which warns about such escapes through
    the process-wide warning filters (not something to touch from a thread)."""
    out = bytearray()
    i, n = 0, len(text)
    while i < n:
        byte = text[i:i + 1]
        if byte != b"\\" or i + 1 >= n:
            out += byte
            i += 1
            continue
        after = text[i + 1:i + 2]
        if after in _C_ESCAPES:
            out += _C_ESCAPES[after]
            i += 2
        elif after.isdigit() and after < b"8":
            length = 1  # up to three octal digits, as many as follow
            while length < 3 and b"0" <= text[i + 1 + length:i + 2 + length] < b"8" and text[i + 1 + length:i + 2 + length]:
                length += 1
            out.append(int(text[i + 1:i + 1 + length], 8) & 0xFF)
            i += 1 + length
        else:
            out += byte + after
            i += 2
    return bytes(out)


# ── what the model sees ──────────────────────────────────────────────────


def render(file: FileDiff, hunks: Iterable[Hunk] | None = None, style: str = "interleaved") -> str:
    """A file's diff with each new-side line numbered, so the model can say
    which line a problem is on. `interleaved` (for small models) keeps the
    lines in order:

        ## path/to/file.py (modified)
        @@ def handler
           41    unchanged
           42 +  added
              -  removed

    `split` (for large ones) shows each hunk's new code, then its old code:

        @@ def handler
        __new hunk__
           41    unchanged
           42 +  added
        __old hunk__
                 unchanged
              -  removed

    A hunk that only removes lines becomes one note, "(3 lines removed after
    line 41)": the model can't comment on removed lines, and on a small model
    the deleted text would only use up its budget.
    """
    lines = [f"## {file.path} ({file.status})"]
    for hunk in hunks if hunks is not None else file.hunks:
        lines.append(f"@@ {hunk.header}".rstrip())
        if not any(line.kind == "+" for line in hunk.lines):
            lines.append(" " * 7 + "   " + _removal_note(hunk))
            continue
        if style == "split":
            lines.append("__new hunk__")
            lines += [f"{line.new:>6} {line.kind}  {line.text}" for line in hunk.lines if line.kind != "-"]
            if any(line.kind == "-" for line in hunk.lines):
                lines.append("__old hunk__")
                lines += [f"{' ' * 6} {line.kind}  {line.text}" for line in hunk.lines if line.kind != "+"]
            continue
        for line in hunk.lines:
            number = f"{line.new:>6}" if line.new is not None else " " * 6
            lines.append(f"{number} {line.kind}  {line.text}")
    return "\n".join(lines)


def with_context(file: FileDiff, text: str | None, lines: int) -> FileDiff:
    """A copy of `file` with up to `lines` more unchanged lines from its new
    text before and after each hunk, for the model to read around the change.
    Findings are placed against the diff itself, never this copy: GitHub only
    takes comments on the lines its own diff shows. The text must agree with
    the diff's new lines, so a stale copy changes nothing."""
    if not text or lines <= 0 or file.binary or not file.hunks:
        return file
    # Split on "\n" only, as parse_diff does: str.splitlines() also breaks on
    # U+2028 and the like, which would shift every later line number.
    source = [line.removesuffix("\r") for line in text.split("\n")]
    if source and source[-1] == "":
        source.pop()  # what follows the file's last newline isn't a line
    if any(line.new is not None and (line.new > len(source) or source[line.new - 1] != line.text)
           for hunk in file.hunks for line in hunk.lines):
        return file
    hunks, covered = [], 0  # covered: the last new line already shown
    for index, hunk in enumerate(file.hunks):
        news = [line.new for line in hunk.lines if line.new is not None]
        if not news or not any(line.kind == "+" for line in hunk.lines):
            hunks.append(hunk)  # removed lines only: rendered as a note, nothing to read around
            covered = max([covered, *news])
            continue
        olds = [line.old for line in hunk.lines if line.old is not None]
        upcoming = [line.new for later in file.hunks[index + 1:] for line in later.lines if line.new is not None]
        stop = min(upcoming, default=len(source) + 1)
        before_shift = hunk.new_start - hunk.old_start  # new - old for the unchanged lines before the hunk
        after_shift = (hunk.new_start + len(news)) - (hunk.old_start + len(olds))
        first, last = min(news), max(news)
        pre = [Line(" ", source[n - 1], n - before_shift, n) for n in range(max(covered + 1, first - lines, 1), first)]
        post = [Line(" ", source[n - 1], n - after_shift, n) for n in range(last + 1, min(stop - 1, last + lines) + 1)]
        hunks.append(Hunk(hunk.old_start - len(pre), hunk.new_start - len(pre), hunk.header, pre + hunk.lines + post))
        covered = post[-1].new if post else last
    return replace(file, hunks=hunks)


def _removal_note(hunk: Hunk) -> str:
    removed = sum(1 for line in hunk.lines if line.kind == "-")
    before = None
    for line in hunk.lines:
        if line.kind == "-":
            break
        before = line.new
    if before is None:
        before = hunk.new_start - 1
    where = f"after line {before}" if before > 0 else "at the start of the file"
    return f"({util.count(removed, 'line')} removed {where})"


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
    chunk_paths: list[list[str]] = field(default_factory=list)  # the paths in each chunk, in the same order


def sendable(file: FileDiff, skip: Callable[[str], bool] = lambda path: False) -> bool:
    """Whether `pack` would send the file: not binary, deleted, empty or skipped."""
    return not (file.binary or file.status == "deleted" or not file.hunks or skip(file.path))


def pack(
    files: list[FileDiff],
    budget_tokens: int,
    max_chunks: int,
    *,
    skip: Callable[[str], bool] = lambda path: False,
    priority: Callable[[FileDiff], float] | None = None,
    count: Callable[[str], int] = approx_tokens,
    style: str = "interleaved",
    large: str = "clip",
) -> Packed:
    """Fill up to `max_chunks` chunks of `budget_tokens`, most important files
    first. A file too big for a chunk on its own is clipped to the hunks that
    fit (`large="clip"`), or left out and listed (`large="skip"`)."""
    packed = Packed([], [], [], [], [])
    candidates = []
    for file in files:
        if sendable(file, skip):
            candidates.append(file)
        else:
            packed.skipped.append(file.path)
    order = priority or (lambda f: f.additions + f.deletions)
    candidates.sort(key=lambda f: -order(f))

    chunk: list[str] = []
    in_chunk: list[str] = []
    used = 0
    for file in candidates:
        text = render(file, style=style)
        size = count(text) + 1
        if size > budget_tokens:
            text = _clip(file, budget_tokens, count, style) if large == "clip" else ""
            size = count(text) + 1 if text else 0
            if not text:
                packed.omitted.append(file.path)
                continue
            clipped = True
        else:
            clipped = False
        if used + size > budget_tokens and chunk:
            packed.chunks.append("\n\n".join(chunk))
            packed.chunk_paths.append(in_chunk)
            chunk, in_chunk, used = [], [], 0
        if len(packed.chunks) >= max_chunks:
            packed.omitted.append(file.path)
            continue
        chunk.append(text)
        in_chunk.append(file.path)
        used += size
        (packed.clipped if clipped else packed.reviewed).append(file.path)
    if chunk and len(packed.chunks) < max_chunks:
        packed.chunks.append("\n\n".join(chunk))
        packed.chunk_paths.append(in_chunk)
    return packed


def _clip(file: FileDiff, budget_tokens: int, count: Callable[[str], int], style: str = "interleaved") -> str:
    """The whole hunks that fit, then as many lines of the next one as fit:
    a new file is a single hunk, and it shouldn't vanish for being long."""
    kept: list[Hunk] = []
    for hunk in file.hunks:
        if count(render(file, kept + [hunk], style)) <= budget_tokens:
            kept.append(hunk)
            continue
        low, high = 0, len(hunk.lines)
        while low < high:
            middle = (low + high + 1) // 2
            part = Hunk(hunk.old_start, hunk.new_start, hunk.header, hunk.lines[:middle])
            if count(render(file, kept + [part], style)) <= budget_tokens:
                low = middle
            else:
                high = middle - 1
        if low:
            kept.append(Hunk(hunk.old_start, hunk.new_start, hunk.header, hunk.lines[:low]))
        break
    return render(file, kept, style) if kept else ""
