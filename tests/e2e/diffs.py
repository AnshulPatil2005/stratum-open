"""Builders for the diffs the edge-case tests feed Stratum."""

from __future__ import annotations


def modified(path: str, old: list[str], new: list[str], *, start: int = 1, header: str = "", no_newline: bool = False) -> str:
    """One file, one hunk replacing `old` lines with `new` lines at `start`."""
    body = [f"diff --git a/{path} b/{path}", f"--- a/{path}", f"+++ b/{path}",
            f"@@ -{start},{len(old)} +{start},{len(new)} @@ {header}".rstrip()]
    body += [f"-{line}" for line in old] + [f"+{line}" for line in new]
    if no_newline:
        body.append("\\ No newline at end of file")
    return "\n".join(body) + "\n"


def added(path: str, lines: list[str]) -> str:
    body = [f"diff --git a/{path} b/{path}", "new file mode 100644", "--- /dev/null", f"+++ b/{path}", f"@@ -0,0 +1,{len(lines)} @@"]
    return "\n".join(body + [f"+{line}" for line in lines]) + "\n"


def deleted(path: str, lines: list[str]) -> str:
    body = [f"diff --git a/{path} b/{path}", "deleted file mode 100644", f"--- a/{path}", "+++ /dev/null", f"@@ -1,{len(lines)} +0,0 @@"]
    return "\n".join(body + [f"-{line}" for line in lines]) + "\n"


def renamed(old: str, new: str, edits: tuple[list[str], list[str]] | None = None) -> str:
    body = [f"diff --git a/{old} b/{new}", "similarity index 90%", f"rename from {old}", f"rename to {new}"]
    if edits:
        before, after = edits
        body += [f"--- a/{old}", f"+++ b/{new}", f"@@ -1,{len(before)} +1,{len(after)} @@"]
        body += [f"-{line}" for line in before] + [f"+{line}" for line in after]
    return "\n".join(body) + "\n"


def copied(old: str, new: str) -> str:
    return "\n".join([f"diff --git a/{old} b/{new}", "similarity index 100%", f"copy from {old}", f"copy to {new}"]) + "\n"


def binary(path: str, *, new: bool = False) -> str:
    body = [f"diff --git a/{path} b/{path}"]
    if new:
        body.append("new file mode 100644")
    body.append(f"Binary files {'/dev/null' if new else 'a/' + path} and b/{path} differ")
    return "\n".join(body) + "\n"


def mode_only(path: str) -> str:
    return "\n".join([f"diff --git a/{path} b/{path}", "old mode 100644", "new mode 100755"]) + "\n"


def symlink(path: str, target: str) -> str:
    return "\n".join([f"diff --git a/{path} b/{path}", "new file mode 120000", "--- /dev/null", f"+++ b/{path}", "@@ -0,0 +1 @@", f"+{target}",
                      "\\ No newline at end of file"]) + "\n"


def submodule(path: str) -> str:
    return "\n".join([f"diff --git a/{path} b/{path}", "index 1111111..2222222 160000", f"--- a/{path}", f"+++ b/{path}", "@@ -1 +1 @@",
                      "-Subproject commit 1111111111111111111111111111111111111111", "+Subproject commit 2222222222222222222222222222222222222222"]) + "\n"


def many_files(count: int, *, lines: int = 3) -> str:
    return "".join(added(f"pkg/mod_{i}.py", [f"x_{i}_{n} = {n}" for n in range(lines)]) for i in range(count))


def one_huge_file(lines: int) -> str:
    return added("big/generated.py", [f"VALUE_{n} = {n}  # line {n}" for n in range(lines)])


def long_line(size: int, path: str = "dist/bundle.min.js") -> str:
    line = "var a=" + ('"\\\\x",' * (size // 6)) + "'unterminated"  # backslashes and an open quote: the regex-killer shape
    return added(path, [line[:size]])
