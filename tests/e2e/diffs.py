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


def minified_bundle(seed: int, size: int = 130_000) -> str:
    """One line of plausible minified JavaScript: short identifiers, string
    literals full of backslash escapes (regex sources, JSON), template
    strings, and an unterminated quote at the end, as real bundles have when
    a regex literal contains one. Backslashes plus an open quote are the shape
    that made the pattern check's old string regex backtrack for minutes."""
    pieces = [
        f'var a{seed}=function(e){{return e.replace(/\\\\/g,"\\\\\\\\").replace(/"/g,"\\\\\\"")}};',
        f'const b{seed}=JSON.parse("{{\\"id\\":{seed},\\"path\\":\\"C:\\\\\\\\Users\\\\\\\\x\\\\\\\\y\\"}}");',
        "function c(e,t){for(var n=0;n<e.length;n++)if(e[n]===t)return n;return-1}",
        f'let d{seed}=`line one\\nline two \\u00e9 ${{seed}}`;',
        'var f=/\\s+(?=[^"]*(?:"[^"]*"[^"]*)*$)/g,g="\\t\\r\\n\\\\",h=\'it\\\'s\';',
        "if(!window.__w)window.__w={q:[],push:function(e){this.q.push(e)}};",
    ]
    line = ""
    index = 0
    while len(line) < size:
        line += pieces[index % len(pieces)]
        index += 1
    return line[:size] + "x=/'/;y=\"unterminated"


def long_line(size: int, path: str = "dist/bundle.min.js") -> str:
    """A diff adding one minified line of about `size` characters."""
    return added(path, [minified_bundle(0, size)])
