"""ShellCheck on the shell scripts a PR adds or changes, when
`checks.shellcheck` is on and `shellcheck` is on PATH (GitHub's Ubuntu
runners have it).

A script is a `.sh` or `.bash` file, or one whose first line is a `sh`, `bash`
or `dash` shebang. A `.sh` file with another shebang (`zsh`, `bats`) isn't
one, and one with none is read as bash, the most lenient of the three. Like
Ruff and Bandit, it runs only on files whose full content the caller passed in
`contents`, and of what ShellCheck reports only issues on lines the PR adds
are kept. Test files are skipped.

Only some of what ShellCheck reports is kept. Its errors, except those in
DROPPED, and the warnings in WARNINGS, each a script doing something other
than its author meant. Its other warnings, and its info and style notes
(SC2086's unquoted variables above all), fire on scripts that work and would
bury the rest. benchmark/results/2026-11-14-shell-checks says how each was
chosen, on merged PRs.

No code from the PR runs, and the PR can't steer ShellCheck. It parses the
text and never runs it, follows no `source`, and with `--norc` reads no
`.shellcheckrc`. The files go in a temporary folder outside any checkout,
under names chosen here, and the program is found on PATH without the folder
the review runs from. A `# shellcheck disable=` comment on an added line was
written by the PR's author, so it's switched off before ShellCheck reads the
file, and a finding it named says so; one already on the default branch is
kept, since only added lines are reported.

It runs as its own process with a time limit, like Bandit, but a longer
one, since ShellCheck is slow on very long scripts.
"""

from __future__ import annotations

import json
import os
import re
import subprocess
import tempfile
from typing import Any

from stratum_open.checks.common import checked_lines, extension, find_program, is_test_path, moved_in
from stratum_open.diff import FileDiff
from stratum_open.findings import Finding

MAX_FILES = 20
# ShellCheck slows down fast on long scripts: about 20 s for acme.sh (295 KB) and over 60 s for nvm.sh
# (243 KB) on a 4-CPU machine. A longer one is named in the summary as not read.
MAX_FILE_CHARS = 100_000
TIMEOUT_SECONDS = 60
EXTENSIONS = (".sh", ".bash")
# ShellCheck's errors that are left out: code -> why.
DROPPED: dict[int, str] = {
    2045: "`for f in $(ls)` breaks on odd file names, but works on the names most scripts see",
    2068: "an unquoted `$@` splits arguments with spaces, but works for the arguments most scripts get",
    2145: "fired on merged code that works: an array in a string handed to `eval`",
    1087: "fired on merged code that works: `$name[-.]` in a regular expression",
    1072: "comes with SC1073 on the same syntax error, which is kept: SC1073 is on the line where it starts, SC1072 often on the last",
}
# ShellCheck's warnings that are kept: code -> what goes wrong.
WARNINGS: dict[int, str] = {
    2069: "The redirections run left to right, so stderr goes where stdout went before `>file`, not into the file.",
    # SC2097, its twin on the assignment, is left out: the two always come together.
    2098: "An assignment in front of a command is passed to that command only: the variable isn't set when this line expands it.",
    2216: "The command doesn't read its input, so what is piped to it is thrown away.",
    2252: "A value can't equal both, so one of the two `!=` tests is always true, and so is the whole condition.",
}
# What a kept error says goes wrong.
_BROKEN = "ShellCheck rates this an error: the line fails, or does something other than what it says, when the script gets to it."
_SHEBANG = re.compile(r"#!\s*(?:\S*/)?(?:env\s+(?:-\S+\s+)*)?(?:sh|bash|dash)(?:\s|$)")
# A directive that switches checks off: `# shellcheck disable=SC2086,SC2154`.
_DISABLE = re.compile(r"(#\s*shellcheck\s(?:[^#\n]*?\s)?)disable=([\w,-]*)")


class ShellCheckError(RuntimeError):
    pass


class ShellCheckTimeout(ShellCheckError):
    pass


def available() -> bool:
    return find_program("shellcheck") is not None


def scope(path: str) -> frozenset[str]:
    """ShellCheck reads `.sh` and `.bash` files outside the tests (and files
    whose shebang says so, which a path can't tell)."""
    return frozenset({"shellcheck"}) if extension(path) in EXTENSIONS and not is_test_path(path) else frozenset()


def worth_reading(file: FileDiff) -> bool:
    """Whether the file's full text is worth fetching to find out: a `.sh` or
    `.bash` file, or a file with no extension whose first line the diff
    doesn't show or shows as a shell shebang."""
    if file.status == "deleted" or file.binary or is_test_path(file.path) or not (file.added() or moved_in(file, scope)):
        return False
    if extension(file.path) in EXTENSIONS:
        return True
    first = file.new_side().get(1)
    return extension(file.path) == "" and (first is None or bool(_SHEBANG.match(first)))


def is_script(file: FileDiff, contents: dict[str, str] | None) -> bool:
    """Whether ShellCheck reads the file: its first line, from its text or the
    diff, is a sh, bash or dash shebang, or it's a `.sh` or `.bash` file with
    no shebang."""
    if is_test_path(file.path):
        return False
    text = (contents or {}).get(file.path)
    first = text.split("\n", 1)[0] if text is not None else file.new_side().get(1)
    if first is not None and first.startswith("#!"):
        return bool(_SHEBANG.match(first))
    return extension(file.path) in EXTENSIONS


def plan(files: list[FileDiff], contents: dict[str, str] | None) -> tuple[list[str], list[str]]:
    """The scripts ShellCheck reads, and the ones it would read but doesn't:
    past MAX_FILES, longer than MAX_FILE_CHARS, or without their text. Files
    the PR edits come first, biggest change first, then files moved here
    from the tests."""
    contents = contents or {}
    candidates = [
        file for file in files
        if file.status != "deleted" and not file.binary and is_script(file, contents) and (file.added() or moved_in(file, scope))
    ]

    def rank(file: FileDiff) -> tuple[bool, int]:
        moved = moved_in(file, scope)
        return moved, -(len(checked_lines(file, contents, scope)) if moved else file.additions)

    readable = [f for f in candidates if f.path in contents and len(contents[f.path]) <= MAX_FILE_CHARS]
    read = [file.path for file in sorted(readable, key=rank)[:MAX_FILES]]
    return read, [file.path for file in candidates if file.path not in read]


def check(files: list[FileDiff], contents: dict[str, str] | None = None) -> list[Finding]:
    if not contents or not available():
        return []
    targets, _ = plan(files, contents)
    if not targets:
        return []
    # A file moved here from the tests counts every line as added.
    added = {file.path: dict(checked_lines(file, contents, scope)) for file in files if file.path in targets}
    sources, disabled = {}, {}
    for path in targets:
        sources[path], disabled[path] = without_disables(contents[path], set(added[path]))
    findings = []
    for issue in run_shellcheck(sources):
        mine = added.get(issue["path"], {})
        on_added = [n for n in range(issue["line"], issue["end_line"] + 1) if n in mine]
        why = kept(issue["code"], issue["level"])
        if why is None or not on_added:
            continue
        code = f"SC{issue['code']}"
        detail = f"ShellCheck reports {code}: {quote(issue['message'])}. {why}"
        if code in disabled[issue["path"]] or "all" in disabled[issue["path"]]:
            detail += f" The PR adds a `# shellcheck disable` comment for {code}. Stratum reports it anyway, because the PR's author added that comment."
        findings.append(Finding(
            title=f"ShellCheck {code}: {_title(issue['message'])}",
            detail=detail,
            severity="high" if issue["level"] == "error" else "medium",
            category="bug",
            path=issue["path"],
            line_start=on_added[0],
            line_end=on_added[0],
            suggestion=f"See https://www.shellcheck.net/wiki/{code} for what it means and how to fix it.",
            source="check",
            rule=f"shellcheck/{code}",
            confidence=0.9,
        ))
    return findings


def kept(code: int, level: str) -> str | None:
    """What goes wrong when ShellCheck reports `code` at `level`, or None when
    the check doesn't keep it."""
    if level == "error":
        return None if code in DROPPED else _BROKEN
    return WARNINGS.get(code) if level == "warning" else None


def quote(message: str) -> str:
    """ShellCheck's message as code, so a name it quotes from the PR can't
    mention anyone or make a link."""
    return "`" + message.replace("`", "'").replace("\n", " ").strip()[:300].rstrip(".") + "`"


def _title(message: str) -> str:
    first = re.split(r"(?<=[a-z)'\"])\.\s", message.replace("`", "'").replace("\n", " "), maxsplit=1)[0].rstrip(".")
    return "`" + (first[:77] + "..." if len(first) > 80 else first) + "`"


def without_disables(text: str, added: set[int]) -> tuple[str, set[str]]:
    """The text with each `# shellcheck disable=` on an added line switched
    off, and the codes those comments named (`SC2086`, or `all`)."""
    lines = text.split("\n")
    named: set[str] = set()
    for number in sorted(added):
        if 0 < number <= len(lines) and _DISABLE.search(lines[number - 1]):
            for match in _DISABLE.finditer(lines[number - 1]):
                named |= {name.strip().upper() if name.strip().lower() != "all" else "all" for name in match.group(2).split(",") if name.strip()}
            lines[number - 1] = _DISABLE.sub(r"\1disabled-by-pr=\2", lines[number - 1])
    return "\n".join(lines), named


def run_shellcheck(sources: dict[str, str], *, timeout: float = TIMEOUT_SECONDS) -> list[dict[str, Any]]:
    """ShellCheck's errors and warnings for each file's text, as plain dicts
    keyed by our path. Each file is written as `<n>.bash`, so one with no
    shebang is read as bash, and ShellCheck runs once over all of them.
    Raises ShellCheckError when it isn't there, doesn't finish, or can't be
    read."""
    program = find_program("shellcheck")
    if program is None:
        raise ShellCheckError("ShellCheck isn't on PATH any more.")
    with tempfile.TemporaryDirectory(prefix="stratum-shellcheck-", ignore_cleanup_errors=True) as folder:
        paths: dict[str, str] = {}
        for index, (path, text) in enumerate(sources.items()):
            name = f"{index}.bash"
            # newline="" writes the line endings as they are, keeping line numbers.
            with open(os.path.join(folder, name), "w", encoding="utf-8", errors="replace", newline="") as handle:
                handle.write(text)
            paths[name] = path
        # --norc: no .shellcheckrc from the folder or the home one; no -x, so no `source` is followed.
        command = [program, "--norc", "--format=json1", "--severity=warning", *paths]
        # SHELLCHECK_OPTS would add options of whoever set it to this run.
        env = {key: value for key, value in os.environ.items() if key.upper() != "SHELLCHECK_OPTS"}
        try:
            result = subprocess.run(command, capture_output=True, text=True, encoding="utf-8", errors="replace", timeout=timeout, cwd=folder, env=env)
        except subprocess.TimeoutExpired:
            raise ShellCheckTimeout(f"ShellCheck took longer than {timeout:.0f} s, so its findings are missing from this review.") from None
    try:
        report = json.loads(result.stdout) if result.returncode in (0, 1) else None
    except ValueError:
        report = None
    comments = report.get("comments") if isinstance(report, dict) else None
    if not isinstance(comments, list):
        raise ShellCheckError(f"ShellCheck stopped with exit code {result.returncode} and no report. It has to be a version with `--format=json1` (0.7.0 or newer).")
    issues = []
    for comment in comments:
        if not isinstance(comment, dict):
            continue
        path = paths.get(os.path.basename(str(comment.get("file") or "")))
        line, end, code = comment.get("line"), comment.get("endLine"), comment.get("code")
        if path is None or not isinstance(line, int) or not isinstance(code, int):
            continue
        issues.append({
            "path": path,
            "code": code,
            "level": str(comment.get("level") or ""),
            "message": str(comment.get("message") or ""),
            "line": line,
            "end_line": max(line, end if isinstance(end, int) else line),
        })
    return issues
